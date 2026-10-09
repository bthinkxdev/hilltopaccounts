from django.urls import path
from . import views
app_name = 'tenancy'
urlpatterns = [path('', views.tenant_list, name='list'), path('<int:pk>/', views.tenant_detail, name='detail'), path('<int:pk>/edit/', views.tenant_edit, name='edit'), path('<int:pk>/move-out/', views.tenant_move_out, name='move_out'), path('partitions/<int:partition_pk>/move-in/', views.tenant_create, name='create')]
