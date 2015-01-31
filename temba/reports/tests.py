import json
from django.core.urlresolvers import reverse
from django.contrib.auth.models import User, Group
from smartmin.tests import SmartminTest, _CRUDLTest
from models import Report
from temba.orgs.models import Org
from temba.tests import TembaTest


class ReportTest(TembaTest):

    def test_create(self):
        self.login(self.admin)

        create_url = reverse('reports.report_create')

        response = self.client.get(create_url)
        self.assertEquals(response.status_code, 302)

        response = self.client.get(create_url, follow=True)
        self.assertEquals(response.status_code, 200)
        self.assertEquals(response.request['PATH_INFO'], reverse('flows.ruleset_analytics'))

        response = self.client.post(create_url, {"title":"first report", "description":"some description", "config":"{}"})
        self.assertEquals(json.loads(response.content)['status'], "error")
        self.assertFalse('report' in json.loads(response.content))

        response = self.client.post(create_url, data='{"title":"first report", "description":"some description", "config":""}', content_type='application/json')
        self.assertEquals(json.loads(response.content)['status'], "success")
        self.assertTrue('report' in json.loads(response.content))
        self.assertTrue('id' in json.loads(response.content)['report'])
        report = Report.objects.get()
        self.assertEquals(report.pk , json.loads(response.content)['report']['id'])

    def test_report_model(self):

        Report.create_report(self.org, self.admin, dict(title="first", description="blah blah text", config=dict(fields=[1,2,3])))
        self.assertEquals(Report.objects.all().count(), 1)

        Report.create_report(self.org, self.admin, dict(title="second", description="yeah yeah yeah", config=dict(fields=[4,5,6])))
        self.assertEquals(Report.objects.all().count(), 2)

        id = Report.objects.filter(title="first")[0].pk
        Report.create_report(self.org, self.admin, dict(title="updated", description="yeah yeah yeahnew description", config=dict(fields=[8,4]), id=id))
        self.assertEquals(Report.objects.all().count(), 2)
        self.assertFalse(Report.objects.filter(title="first"))
        self.assertEquals(id, Report.objects.get(title="updated").pk)
