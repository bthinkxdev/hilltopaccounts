from django.urls import path
from . import views
app_name = 'villas'
urlpatterns = [path('villas/', views.villa_list, name='villa_list'), path('villas/new/', views.villa_create, name='villa_create'), path('villas/<int:pk>/', views.villa_detail, name='villa_detail'), path('villas/<int:pk>/archive/', views.villa_archive, name='villa_archive'), path('partitions/', views.partition_list, name='partition_list'), path('partitions/new/', views.partition_create, name='partition_create'), path('partitions/<int:pk>/', views.partition_detail, name='partition_detail'), path('partitions/<int:pk>/archive/', views.partition_archive, name='partition_archive'), path('villas/<int:villa_pk>/partitions/new/', views.partition_create, name='partition_create')]
