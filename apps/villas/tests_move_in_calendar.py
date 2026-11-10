"""The move-in calendar hides past months for non-Admins (display only); the Admin keeps every date."""
from django.urls import reverse

from apps.villas.services import create_partition
from .tests_villa_stats import THIS_MONTH, VillaStatsBase


class MoveInCalendar(VillaStatsBase):

    def form_html(self, who):
        self.login(who)
        vacant = create_partition(villa=self.villa, name='Vacant X', created_by=self.owner)
        r = self.client.get(reverse('tenancy:create', args=[vacant.pk]))
        return r.status_code, r.content.decode()

    def test_admin_has_no_minimum_date(self):
        status, html = self.form_html('owner')
        self.assertEqual(status, 200)
        self.assertNotIn(f'min="{THIS_MONTH.isoformat()}"', html)

    def test_staff_calendar_starts_this_month(self):
        status, html = self.form_html('staff')
        self.assertEqual(status, 200)
        if True:
            self.assertIn(f'min="{THIS_MONTH.isoformat()}"', html)
