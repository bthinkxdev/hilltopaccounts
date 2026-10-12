from django.urls import path
from . import views
app_name = 'listings'
urlpatterns = [
    path('', views.listing_list, name='list'),
    path('listings/<int:pk>/', views.listing_detail, name='detail'),
    path('listings/photo/<int:pk>/', views.listing_photo, name='photo'),
]
