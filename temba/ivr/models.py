from __future__ import unicode_literals

from django.core.urlresolvers import reverse
from django.db import models
from django.conf import settings
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from smartmin.models import SmartModel
from temba.contacts.models import Contact, TEL_SCHEME
from temba.flows.models import Flow, FlowStep, ActionLog, FlowRun
from temba.channels.models import Channel
from temba.orgs.models import Org, TopUp

PENDING = 'P'
QUEUED = 'Q'
RINGING = 'R'
IN_PROGRESS = 'I'
COMPLETED = 'D'
BUSY = 'B'
FAILED = 'F'
NO_ANSWER = 'N'
CANCELED = 'C'

DONE = [ COMPLETED, BUSY, FAILED, NO_ANSWER, CANCELED ]

INCOMING = 'I'
OUTGOING = 'O'

FLOW = 'F'

DIRECTION_CHOICES = ((INCOMING, "Incoming"),
                     (OUTGOING, "Outgoing"))

TYPE_CHOICES = ((FLOW, "Flow"),)

STATUS_CHOICES = ((QUEUED, "Queued"),
                  (RINGING, "Ringing"),
                  (IN_PROGRESS, "In Progress"),
                  (COMPLETED, "Complete"),
                  (BUSY, "Busy"),
                  (FAILED, "Failed"),
                  (NO_ANSWER, "No Answer"),
                  (CANCELED, "Canceled"))


class IVRCall(SmartModel):

    external_id = models.CharField(max_length=255,
                                   help_text="The external id for this call, our twilio id usually")
    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default=PENDING,
                              help_text="The status of this call")
    channel = models.ForeignKey(Channel,
                                help_text="The channel that made this call")
    contact = models.ForeignKey(Contact,
                                help_text="Who this call is with")
    direction = models.CharField(max_length=1, choices=DIRECTION_CHOICES,
                                 help_text="The direction of this call, either incoming or outgoing")
    flow = models.ForeignKey(Flow, null=True,
                             help_text="The flow this call was part of")
    started_on = models.DateTimeField(null=True, blank=True,
                                      help_text="When this call was connected and started")
    ended_on = models.DateTimeField(null=True, blank=True,
                                    help_text="When this call ended")
    org = models.ForeignKey(Org,
                            help_text="The organization this call belongs to")
    call_type = models.CharField(max_length=1, choices=TYPE_CHOICES, default=FLOW,
                                 help_text="What sort of call is this")
    duration = models.IntegerField(default=0, null=True,
                                   help_text="The length of this call")


    @classmethod
    def create_outgoing(cls, channel, contact, flow, user, call_type=FLOW):
        call = IVRCall.objects.create(channel=channel, contact=contact, flow=flow, direction=OUTGOING,
                                      org=channel.org, created_by=user, modified_by=user, call_type=call_type)
        return call

    @classmethod
    def create_incoming(cls, channel, contact, flow, user, call_type=FLOW):
        call = IVRCall.objects.create(channel=channel, contact=contact, flow=flow, direction=INCOMING,
                                      org=channel.org, created_by=user, modified_by=user, call_type=call_type)
        return call

    @classmethod
    def hangup_test_call(cls, flow):
        # if we have an active call, hang it up
        test_call = IVRCall.objects.filter(contact__is_test=True, flow=flow)
        if test_call:
            test_call = test_call[0]
            if not test_call.is_done():
                test_call.hangup()
                # by deleting this, we'll be dropping twilio's status update
                # when the hanging up is completed, for test calls, we are okay with that
                test_call.delete()

    def is_flow(self):
        return self.call_type == FLOW

    def is_done(self):
        return self.status in DONE

    def hangup(self):
        if not self.is_done():
            client = self.channel.get_ivr_client()
            if client and self.external_id:
                print "Hanging up %s for %s" % (self.external_id, self.get_status_display())
                client.calls.hangup(self.external_id)

    def do_update_call(self, qs=None):
        client = self.channel.get_ivr_client()
        if client:
            try:
                url = "http://%s%s" % (settings.TEMBA_HOST, reverse('ivr.ivrcall_handle', args=[self.pk]))
                if qs:
                    url = "%s?%s" % (url, qs)
                client.calls.update(self.external_id, url=url)
            except Exception as e: # pragma: no cover
                import traceback
                traceback.print_exc()
                self.status = FAILED
                self.save()

    def do_start_call(self, qs=None):
        client = self.channel.get_ivr_client()
        if client:
            try:
                url = "https://%s%s" % (settings.TEMBA_HOST, reverse('ivr.ivrcall_handle', args=[self.pk]))
                if qs:
                    url = "%s?%s" % (url, qs)

                tel = None

                # if we are working with a test contact, look for user settings
                if self.contact.is_test:
                    user_settings = self.created_by.get_settings()
                    if user_settings.tel:
                        tel = user_settings.tel
                        run = FlowRun.objects.filter(call=self)
                        if run:
                            ActionLog.create_action_log(run[0],
                                                        "Placing test call to %s" % user_settings.get_tel_formatted())
                if not tel:
                    tel_urn = self.contact.get_urn(TEL_SCHEME)
                    if not tel_urn:
                        raise ValueError("Can't call contact with no tel URN")

                    tel = tel_urn.path

                client.start_call(self, to=tel, from_=self.channel.address, status_callback=url)

            except Exception:  # pragma: no cover
                import traceback
                traceback.print_exc()
                self.status = FAILED
                self.save()

    def update_status(self, status, duration):
        """
        Updates our status from a twilio status string
        """
        if status == 'queued':
            self.status = QUEUED
        elif status == 'ringing':
            self.status = RINGING
        elif status == 'no-answer':
            self.status = NO_ANSWER
        elif status == 'in-progress':
            if self.status != IN_PROGRESS:
                self.started_on = timezone.now()
            self.status = IN_PROGRESS
        elif status == 'completed':
            if self.contact.is_test:
                run = FlowRun.objects.filter(call=self)
                if run:
                    ActionLog.create_action_log(run[0], _("Call ended."))
            self.status = COMPLETED
        elif status == 'busy':
            self.status = BUSY
        elif status == 'failed':
            self.status = FAILED
        elif status == 'canceled':
            self.status = CANCELED

        self.duration = duration

    def get_duration(self):
        """
        Either gets the set duration as reported by twilio, or tries to calculate
        it from the aproximate time it was started
        """
        duration = self.duration
        if not duration and self.status == 'I' and self.started_on:
            duration = (timezone.now() - self.started_on).seconds

        if not duration:
            duration = 0

        return duration

    def start_call(self):
        from .tasks import start_call_task
        start_call_task.delay(self.pk)


class IVRAction(models.Model):

    DIRECTION_CHOICES = ((INCOMING, "Incoming"),
                         (OUTGOING, "Outgoing"))

    org = models.ForeignKey(Org, related_name='ivr_actions',
                            help_text="The org this message is connected to")

    call = models.ForeignKey(IVRCall, related_name='ivr_actions_for_call',
                             help_text="The call this action is a part of")

    created_on = models.DateTimeField(help_text="When this message was created", auto_now_add=True)

    direction = models.CharField(max_length=1, choices=DIRECTION_CHOICES,
                                 help_text="The direction for this action, either incoming or outgoing")

    step = models.ForeignKey(FlowStep, null=True, blank=True, related_name='ivr_actions_for_step',
                             help_text="The step that created this action")

    topup = models.ForeignKey(TopUp, null=True, blank=True, related_name='ivr', on_delete=models.SET_NULL,
                              help_text="The topup that this action was deducted from")

    @classmethod
    def create_action(cls, call, step):
        # costs 1 credit to perform an IVR action
        topup_id = None
        if not call.contact.is_test:
            topup_id = call.org.decrement_credit()

        IVRAction.objects.create(call=call, org=call.org, step=step, topup_id=topup_id)
