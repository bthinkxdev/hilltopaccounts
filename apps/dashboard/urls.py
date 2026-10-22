from django.urls import path
from . import views
app_name = 'dashboard'
urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path('search/', views.global_search, name='search'),
    path('export/csv/', views.export_financials_csv, name='export_financials_csv'),
]
