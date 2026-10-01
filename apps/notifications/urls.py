from django.urls import path
from . import views
app_name = 'notifications'
urlpatterns = [path('', views.notification_list, name='list'), path('read-all/', views.notification_read_all, name='read_all'), path('<int:pk>/read/', views.notification_read, name='read')]
