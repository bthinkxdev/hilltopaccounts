from django.urls import path
from . import views
app_name = 'businesses'
urlpatterns = [path('', views.business_list, name='list'), path('new/', views.business_create, name='create'), path('<int:pk>/', views.business_detail, name='detail'), path('<int:pk>/archive/', views.business_archive, name='archive')]
