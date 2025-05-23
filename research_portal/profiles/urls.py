from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from .views import RegisterView, PasswordResetView, login_view, dashboard_view, profile_view, edit_profile, export_profile, logout_view, settings_view, notifications_view, privacy_view, update_profile_image, mark_notification_as_read
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.contrib.auth import views as auth_views

app_name = 'profiles'

# Dashboard views
urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    
    path('login/', login_view, name='login'),
    path('password-reset/', PasswordResetView.as_view(), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(template_name='profiles/password_reset_done.html'), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(template_name='profiles/password_reset_confirm.html'), name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(template_name='profiles/password_reset_complete.html'), name='password_reset_complete'),
    path('dashboard/', login_required(dashboard_view), name='dashboard'),
    path('profile/', profile_view, name='profile'),
    path('profile/update-image/', update_profile_image, name='update_profile_image'),
    path('profile/export/<str:export_format>/', export_profile, name='export_profile'),
    path('profile/edit/', edit_profile, name='edit_profile'),
    path('profile/settings/', settings_view, name='settings'),
    path('profile/notifications/', notifications_view, name='notifications'),
    path('profile/notifications/mark-as-read/', mark_notification_as_read, name='mark_notification_read'),
    path('profile/privacy/', privacy_view, name='privacy'),
    path('logout/', logout_view, name='logout'),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
