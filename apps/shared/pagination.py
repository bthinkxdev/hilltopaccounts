from django.core.paginator import Paginator

def paginate_queryset(request, queryset, per_page=25):
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get('page'))
