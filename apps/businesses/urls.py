from django.urls import path
from . import views
app_name = 'businesses'
urlpatterns = [
    path('', views.business_list, name='list'),
    path('new/', views.business_create, name='create'),
    path('<int:pk>/', views.business_detail, name='detail'),
    path('<int:pk>/archive/', views.business_archive, name='archive'),
    path('<int:pk>/unarchive/', views.business_unarchive, name='unarchive'),
    path('partners/', views.partner_list, name='partner_list'),
    path('partners/new/', views.partner_create, name='partner_create'),
    path('partners/<int:pk>/', views.partner_detail, name='partner_detail'),
    path('partners/<int:pk>/edit/', views.partner_edit, name='partner_edit'),
    path('partners/<int:partner_pk>/shares/new/', views.partner_share_create, name='partner_share_create'),
    path('partners/shares/<int:pk>/edit/', views.partner_share_edit, name='partner_share_edit'),
    path('partners/shares/<int:pk>/delete/', views.partner_share_delete, name='partner_share_delete'),
    path('partners/<int:partner_pk>/payouts/new/', views.partner_payout_create, name='partner_payout_create'),
    path('partners/payouts/<int:pk>/delete/', views.partner_payout_delete, name='partner_payout_delete'),
    path('villas/<int:villa_pk>/payout-partners/', views.villa_partner_payout_batch, name='villa_partner_payout_batch'),
]
