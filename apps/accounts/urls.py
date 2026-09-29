from django.urls import path
from . import staff_views, views
app_name = 'accounts'
urlpatterns = [path('login/', views.AccountLoginView.as_view(), name='login'), path('logout/', views.AccountLogoutView.as_view(), name='logout'), path('staff/', staff_views.staff_list, name='staff_list'), path('staff/new/', staff_views.staff_create, name='staff_create'), path('staff/<int:pk>/', staff_views.staff_detail, name='staff_detail'), path('staff/<int:pk>/deactivate/', staff_views.staff_deactivate, name='staff_deactivate'), path('staff/<int:staff_pk>/assignments/new/', staff_views.assignment_create, name='assignment_create'), path('staff/assignments/<int:pk>/remove/', staff_views.assignment_remove, name='assignment_remove')]
