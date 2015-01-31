import json
import urllib2
from temba.contacts.models import Contact, TEL_SCHEME
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.files.temp import NamedTemporaryFile
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View
from django.conf import settings
from twilio import twiml

from temba.utils import build_json_response
from temba.flows.models import Flow, ActionSet
from .models import IVRCall, IN_PROGRESS

class CallHandler(View):

    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super(CallHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return HttpResponse("ILLEGAL METHOD")

    def post(self, request, *args, **kwargs):
        from twilio.util import RequestValidator

        call = IVRCall.objects.filter(pk=kwargs['pk']).first()

        if not call:
            return HttpResponse("Not found", status=404)

        client = call.channel.get_ivr_client()
        if request.REQUEST.get('hangup', 0):
            if not request.user.is_anonymous():
                user_org = request.user.get_org()
                if user_org and user_org.pk == call.org.pk:
                    client.calls.hangup(call.external_id)
                    return HttpResponse(json.dumps(dict(status='Canceled')), content_type="application/json")
                else:
                    return HttpResponse("Not found", status=404)

        validator = RequestValidator(client.auth[1])
        signature = request.META.get('HTTP_X_TWILIO_SIGNATURE', '')

        base_url = settings.TEMBA_HOST
        url = "https://%s%s" % (base_url, request.get_full_path())

        # make sure this is coming from twilio
        if validator.validate(url, request.POST, signature):
            call.update_status(request.POST.get('CallStatus', None),
                               request.POST.get('CallDuration', None))
            call.save()

            if call.status == IN_PROGRESS:
                if call.is_flow():
                    response = Flow.handle_call(call, request.POST)
                    return HttpResponse(unicode(response))
            else:
                return build_json_response(dict(message="Updated call status"))

        else:  # pragma: no cover
            # raise an exception that things weren't properly signed
            raise ValidationError("Invalid request signature")

        return build_json_response(dict(message="Unhandled"))
