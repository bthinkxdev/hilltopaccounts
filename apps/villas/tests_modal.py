"""Action pages open as popups. The popup is only another way to show and submit the same server view: the data
saved, the validation and the messages must be identical to the page-based flow."""
from django.urls import reverse
from .tests_villa_stats import LAST_MONTH, VillaStatsBase

MODAL = {'HTTP_X_MODAL': '1'}


class ActionPopupTests(VillaStatsBase):

    def setUp(self):
        super().setUp()
        self.login('owner')
        self.url = reverse('expenses:create_for_villa', args=[self.villa.pk])
        self.good = {'villa': self.villa.pk, 'name': 'Door lock', 'amount': '80.00', 'date': LAST_MONTH.isoformat()}

    def test_modal_request_gets_a_bare_fragment_with_a_cancel(self):
        r = self.client.get(self.url, **MODAL)
        self.assertContains(r, 'data-modal-fragment')
        self.assertContains(r, 'data-modal-cancel')
        self.assertNotContains(r, '<html')
        self.assertNotContains(r, 'class="sidebar"')

    def test_normal_request_is_the_full_page_as_before(self):
        r = self.client.get(self.url)
        self.assertContains(r, 'class="sidebar"')
        self.assertNotContains(r, 'data-modal-fragment')
        self.assertIn('X-Modal', r['Vary'])

    def test_valid_post_saves_and_hands_the_redirect_back_without_losing_the_message(self):
        from apps.expenses.models import Expense
        r = self.client.post(self.url, self.good, **MODAL)
        self.assertEqual(r.status_code, 200)
        target = r['X-Modal-Redirect']
        self.assertEqual(Expense.objects.filter(villa=self.villa, category__name='Door lock').count(), 1)
        self.assertContains(self.client.get(target), 'Door lock')  # the success message is still waiting for the next page

    def test_invalid_post_comes_back_as_the_form_with_errors_and_saves_nothing(self):
        from apps.expenses.models import Expense
        r = self.client.post(self.url, {**self.good, 'amount': '0'}, **MODAL)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('X-Modal-Redirect', r)
        self.assertContains(r, 'data-modal-fragment')
        self.assertContains(r, 'field__error')
        self.assertEqual(Expense.objects.filter(villa=self.villa).count(), 0)

    def test_the_same_post_without_the_popup_still_redirects_and_saves_the_same_row(self):
        from apps.expenses.models import Expense
        r = self.client.post(self.url, self.good)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Expense.objects.get(villa=self.villa, category__name='Door lock').amount, 80)

    def test_every_kind_of_action_page_renders_as_a_fragment(self):
        tenant = self.p1.current_tenant
        urls = [reverse('tenancy:move_out', args=[tenant.pk]), reverse('tenancy:edit', args=[tenant.pk]), reverse('tenancy:deposit_edit', args=[tenant.pk]),
                reverse('billing:charge_create', args=[self.p1.pk]), reverse('villas:villa_edit', args=[self.villa.pk]), reverse('villas:partition_create'),
                reverse('villas:villa_sell', args=[self.villa.pk]), reverse('villas:villa_archive', args=[self.villa.pk]), reverse('villas:villa_photo_add', args=[self.villa.pk]),
                reverse('businesses:create'), reverse('businesses:partner_create'), reverse('accounts:staff_create'), reverse('accounts:password_change'),
                reverse('expenses:create'), reverse('expenses:recurring_create', args=[self.villa.pk]), reverse('businesses:villa_partner_payout_batch', args=[self.villa.pk])]
        for url in urls:
            r = self.client.get(url, **MODAL)
            if r.status_code == 200 and 'X-Modal-Redirect' in r:
                continue  # a view that sends the user elsewhere (e.g. nothing to do) hands that back instead
            self.assertEqual(r.status_code, 200, url)
            self.assertContains(r, 'data-modal-fragment', msg_prefix=url)
            self.assertContains(r, 'data-modal-cancel', msg_prefix=url)

    def test_pages_carry_the_popup_shell_and_mark_their_action_links(self):
        r = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]))
        self.assertContains(r, 'id="action-modal"')
        self.assertContains(r, 'js/modal.js')
        self.assertContains(r, f'href="{reverse("expenses:create_for_villa", args=[self.villa.pk])}" data-modal')

    def test_a_permission_failure_is_not_turned_into_a_popup(self):
        self.login('staff')
        r = self.client.get(reverse('businesses:create'), **MODAL)
        self.assertEqual(r.status_code, 403)


class ActionListTests(VillaStatsBase):

    def test_the_action_page_list_matches_the_routes_that_really_render_an_action_template(self):
        import inspect
        from django.urls import URLPattern, URLResolver, get_resolver
        from apps.shared.modal import ACTION_TEMPLATES, ACTION_URL_NAMES
        found = set()

        def walk(resolver):
            for p in resolver.url_patterns:
                if isinstance(p, URLResolver):
                    walk(p)
                elif isinstance(p, URLPattern) and any(t in inspect.getsource(p.callback) for t in ACTION_TEMPLATES):
                    found.add(f'{resolver.namespace}:{p.name}')
        walk(get_resolver())
        self.assertEqual(found, set(ACTION_URL_NAMES))

    def test_alert_and_dashboard_links_to_action_pages_open_as_popups(self):
        from apps.shared.modal import is_action_url
        self.assertTrue(is_action_url(reverse('expenses:mark_paid', args=[1]) + '?next=/'))
        self.assertTrue(is_action_url(reverse('cash:handover_submit')))
        self.assertFalse(is_action_url(reverse('expenses:list') + '?x=1'))
        self.assertFalse(is_action_url('https://elsewhere.example/expenses/new/'))
        self.assertFalse(is_action_url('#'))
        self.login('owner')
        r = self.client.get(reverse('dashboard:home'))
        self.assertContains(r, f'href="{reverse("expenses:create")}?next={reverse("dashboard:home")}" data-modal')
