from django.urls import path
from . import views
app_name = 'cash'
urlpatterns = [path('', views.handover_list, name='handover_list'), path('staff/', views.staff_accountability, name='staff_accountability'), path('submit/', views.handover_submit, name='handover_submit'), path('<int:pk>/', views.handover_detail, name='handover_detail'), path('<int:pk>/confirm/', views.handover_confirm, name='handover_confirm'), path('<int:pk>/reject/', views.handover_reject, name='handover_reject')]
