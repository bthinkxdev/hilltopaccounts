from django.urls import path
from . import views
app_name = 'expenses'
urlpatterns = [path('', views.expense_list, name='list'), path('new/', views.expense_create, name='create'), path('<int:pk>/cancel/', views.expense_cancel, name='cancel'), path('<int:pk>/reverse/', views.expense_reverse, name='reverse')]
