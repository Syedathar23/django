from django.urls import path
from analytics import views

app_name = 'analytics'

urlpatterns = [
    # Main Views
    path('', views.home_view, name='home'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('sentiment/', views.sentiment_view, name='sentiment'),
    path('trends/', views.trends_view, name='trends'),
    path('network/', views.network_view, name='network'),
    path('stream/', views.stream_view, name='stream'),
    
    # API Endpoints
    path('api/network-data/', views.api_network_data, name='api_network_data'),
    path('api/cascade-data/<int:post_id>/', views.api_cascade_data, name='api_cascade_data'),
    path('api/chart-data/', views.api_chart_data, name='api_chart_data'),
    path('api/simulate-surge/', views.api_simulate_surge, name='api_simulate_surge'),
    path('api/ingest-post/', views.api_ingest_post, name='api_ingest_post'),
    path('api/generate-brief/', views.api_generate_brief, name='api_generate_brief'),
    path('api/fetch-live-social/', views.api_fetch_live_social, name='api_fetch_live_social'),
    path('api/collector-status/', views.api_collector_status, name='api_collector_status'),
    path('api/save-api-keys/', views.api_save_api_keys, name='api_save_api_keys'),
    path('api/clear-mock-data/', views.api_clear_mock_data, name='api_clear_mock_data'),
]
