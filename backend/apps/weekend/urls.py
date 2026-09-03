from django.urls import path

from . import views

app_name = "weekend"

urlpatterns = [
    path("", views.index, name="index"),
    path("mine/", views.my_stay, name="my_stay"),
    path("stay/<int:stay_id>/review/", views.review, name="review"),
    path("stay/<int:stay_id>/cancel/", views.cancel, name="cancel"),
    path("guest/add/", views.add_guest, name="add_guest"),
    path("check/<str:weekend>/<str:check_type>/", views.check_session, name="check_session"),
    path("check/<int:session_id>/stay/<int:stay_id>/", views.record_check, name="record_check"),
    path("check/<int:session_id>/close/", views.close_check, name="close_check"),
    path("check/<int:session_id>/reopen/", views.reopen_check, name="reopen_check"),
    path("roster/<str:weekend>/", views.roster, name="roster"),
]
