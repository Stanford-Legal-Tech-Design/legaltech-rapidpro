import json
from temba.contacts.models import Contact
from django.contrib.auth.models import Group
from django.core.urlresolvers import reverse
from mock import patch
import mock
from temba.flows.models import Flow, FAILED, FlowRun, ActionLog
from temba.ivr.models import IVRCall, OUTGOING, IN_PROGRESS, QUEUED, COMPLETED, BUSY, CANCELED, RINGING, NO_ANSWER
from temba.ivr.clients import TwilioClient
from temba.channels.models import TWILIO, CALL, ANSWER
from temba.tests import TembaTest
from twilio.rest import TwilioRestClient, UNSET_TIMEOUT
from twilio.util import RequestValidator


class MockRequestValidator(RequestValidator):

    def __init__(self, token):
        pass

    def validate(self, url, post, signature):
        return True

class MockTwilioClient(TwilioClient):

    def __init__(self, sid, token):
        self.applications = MockTwilioClient.MockApplications()
        self.calls = MockTwilioClient.MockCalls()
        self.auth = ['', 'FakeRequestToken']

    class MockCall():
        def __init__(self, to=None, from_=None, url=None, status_callback=None):
            self.to = to
            self.from_ = from_
            self.url = url
            self.status_callback = status_callback
            self.sid = 'CallSid'

    class MockApplication():
        def __init__(self, friendly_name):
            self.friendly_name = friendly_name
            self.sid = 'TwilioTestSid'

    class MockApplications():
        def __init__(self, *args):
            pass

        def list(self, friendly_name=None):
            return [MockTwilioClient.MockApplication(friendly_name)]

    class MockCalls():
        def __init__(self):
            pass

        def create(self, to=None, from_=None, url=None, status_callback=None):
            return MockTwilioClient.MockCall(to=to, from_=from_, url=url, status_callback=status_callback)

        def hangup(self, external_id):
            print "Hanging up %s on Twilio" % external_id

        def update(self, external_id, url):
            print "Updating call for %s to url %s" % (external_id, url)


class IVRTests(TembaTest):

    def setUp(self):

        super(IVRTests, self).setUp()

        # configure our account to be IVR enabled
        self.channel.channel_type = TWILIO
        self.channel.role = CALL + ANSWER
        self.channel.save()
        self.admin.groups.add(Group.objects.get(name="Beta"))
        self.login(self.admin)

    @mock.patch('temba.orgs.models.TwilioRestClient', MockTwilioClient)
    @mock.patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @mock.patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_ivr_options(self):

        # should be able to create an ivr flow
        self.assertTrue(self.org.supports_ivr())
        self.assertTrue(self.admin.groups.filter(name="Beta"))
        self.assertContains(self.client.get(reverse('flows.flow_create')), 'Phone Call')

        # no twilio config yet
        self.assertFalse(self.org.is_connected_to_twilio())
        self.assertIsNone(self.org.get_twilio_client())

        # connect it and check our client is configured
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN")
        self.org.save()
        self.assertTrue(self.org.is_connected_to_twilio())
        self.assertIsNotNone(self.org.get_twilio_client())

        # import an ivr flow
        self.import_file('call-me-maybe')

        # make sure our flow is there as expected
        flow = Flow.objects.filter(name='Call me maybe').first()
        self.assertEquals('callme', flow.triggers.all().first().keyword)

        user_settings = self.admin.get_settings()
        user_settings.tel = '+18005551212'
        user_settings.save()

        # start our flow`
        eric = self.create_contact('Eric Newcomer', number='+13603621737')
        eric.is_test = True
        eric.save()
        Contact.set_simulation(True)
        flow.start([], [eric])

        # should be using the usersettings number in test mode
        self.assertEquals('Placing test call to +1 800-555-1212', ActionLog.objects.all().first().text)

        # we should have an outbound ivr call now
        call = IVRCall.objects.filter(direction=OUTGOING).first()

        self.assertEquals(0, call.get_duration())
        self.assertIsNotNone(call)
        self.assertEquals('CallSid', call.external_id)

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)
        self.assertContains(response, '<Say>Would you like me to call you? Press one for yes, two for no, or three for maybe.</Say>')

        # updated our status and duration accordingly
        call = IVRCall.objects.get(pk=call.pk)
        self.assertEquals(20, call.duration)
        self.assertEquals(IN_PROGRESS, call.status)

        # should mention our our action log that we read a message to them
        run = FlowRun.objects.all().first()
        logs = ActionLog.objects.filter(run=run).order_by('-pk')
        self.assertEquals(2, len(logs))
        self.assertEquals('Read message "Would you like me to call you? Press one for yes, two for no, or three for maybe."', logs.first().text)

        # press the number 4 (unexpected)
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), dict(Digits=4))
        self.assertContains(response, '<Say>Press one, two, or three. Thanks.</Say>')

        # now let's have them press the number 3 (for maybe)
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), dict(Digits=3))
        self.assertContains(response, '<Say>This might be crazy.</Say>')

        # twilio would then disconnect the user and notify us of a completed call
        self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), dict(CallStatus='completed'))
        self.assertEquals(COMPLETED, IVRCall.objects.get(pk=call.pk).status)

        # simulation gets flipped off by middleware, and this unhandled message doesn't flip it back on
        self.assertFalse(Contact.get_simulation())

        # test other our call status mappings with twilio
        def test_status_update(call_to_update, twilio_status, temba_status):
            call_to_update.update_status(twilio_status, 0)
            call_to_update.save()
            self.assertEquals(temba_status, IVRCall.objects.get(pk=call_to_update.pk).status)

        test_status_update(call, 'queued', QUEUED)
        test_status_update(call, 'ringing', RINGING)
        test_status_update(call, 'canceled', CANCELED)
        test_status_update(call, 'busy', BUSY)
        test_status_update(call, 'failed', FAILED)
        test_status_update(call, 'no-answer', NO_ANSWER)

        # explicitly hanging up an in progress call should remove it
        call.update_status('in-progress', 0)
        call.save()
        IVRCall.hangup_test_call(flow)
        self.assertIsNone(IVRCall.objects.filter(pk=call.pk).first())

    @mock.patch('temba.orgs.models.TwilioRestClient', MockTwilioClient)
    @mock.patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @mock.patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_rule_first_ivr_flow(self):
        # connect it and check our client is configured
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN")
        self.org.save()

        # import an ivr flow
        self.import_file('rule-first-ivr')
        flow = Flow.objects.filter(name='Rule First IVR').first()

        user_settings = self.admin.get_settings()
        user_settings.tel = '+18005551212'
        user_settings.save()

        # start our flow`
        eric = self.create_contact('Eric Newcomer', number='+13603621737')
        eric.is_test = True
        eric.save()
        Contact.set_simulation(True)
        flow.start([], [eric])

        # should be using the usersettings number in test mode
        self.assertEquals('Placing test call to +1 800-555-1212', ActionLog.objects.all().first().text)

        # we should have an outbound ivr call now
        call = IVRCall.objects.filter(direction=OUTGOING).first()

        self.assertEquals(0, call.get_duration())
        self.assertIsNotNone(call)
        self.assertEquals('CallSid', call.external_id)

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)
        self.assertContains(response, '<Say>Thanks for calling!</Say>')
