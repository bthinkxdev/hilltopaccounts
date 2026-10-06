from django.urls import path
from . import views
app_name = 'villas'
urlpatterns = [
    path('villas/', views.villa_list, name='villa_list'),
    path('villas/new/', views.villa_create, name='villa_create'),
    path('villas/<int:pk>/', views.villa_detail, name='villa_detail'),
    path('villas/<int:pk>/edit/', views.villa_edit, name='villa_edit'),
    path('villas/<int:pk>/archive/', views.villa_archive, name='villa_archive'),
    path('villas/<int:villa_pk>/photos/add/', views.photo_add, name='villa_photo_add'),
    path('partitions/<int:partition_pk>/photos/add/', views.photo_add, name='partition_photo_add'),
    path('photos/<int:pk>/', views.photo_file, name='photo_file'),
    path('photos/<int:pk>/delete/', views.photo_delete, name='photo_delete'),
    path('partitions/', views.partition_list, name='partition_list'),
    path('partitions/new/', views.partition_create, name='partition_create'),
    path('partitions/<int:pk>/', views.partition_detail, name='partition_detail'),
    path('partitions/<int:pk>/edit/', views.partition_edit, name='partition_edit'),
    path('partitions/<int:pk>/archive/', views.partition_archive, name='partition_archive'),
    path('villas/<int:villa_pk>/partitions/new/', views.partition_create, name='partition_create'),
]
