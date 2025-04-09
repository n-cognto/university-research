from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.contrib.auth.forms import PasswordResetForm
from .models import Profile
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages

class UserRegistrationForm(forms.ModelForm):
    first_name = forms.CharField()
    middle_name = forms.CharField(required=False)
    last_name = forms.CharField()
    phone_number = forms.CharField()
    id_number = forms.CharField()
    dob = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    gender = forms.ChoiceField(choices=[('male', 'Male'), ('female', 'Female')])
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password")

    class Meta:
        model = User
        fields = ('email',)  # Only include email field from User model

    def clean_id_number(self):
        id_number = self.cleaned_data.get('id_number')
        if Profile.objects.filter(id_number=id_number).exists():
            raise forms.ValidationError("A user with that ID number already exists.")
        return id_number

    def clean_phone_number(self):
        phone_number = self.cleaned_data.get('phone_number')
        if Profile.objects.filter(phone_number=phone_number).exists():
            raise forms.ValidationError("A user with that phone number already exists.")
        return phone_number

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("A user with that email already exists.")
        return email

    def clean_password2(self):
        cd = self.cleaned_data
        if cd.get('password') != cd.get('password2'):
            raise forms.ValidationError("Passwords don't match.")
        return cd.get('password2')

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data['email']  # Set username to email
        user.set_password(self.cleaned_data['password'])
        if commit:
            user.save()
            Profile.objects.create(
                user=user,
                middle_name=self.cleaned_data.get('middle_name', ''),
                phone_number=self.cleaned_data['phone_number'],
                id_number=self.cleaned_data['id_number'],
                dob=self.cleaned_data['dob'],
                gender=self.cleaned_data['gender']
            )
        return user

class CustomPasswordResetForm(PasswordResetForm):
    email = forms.EmailField(max_length=254)

class LoginForm(forms.Form):
    id_number = forms.CharField(max_length=50, label="ID Number")
    password = forms.CharField(widget=forms.PasswordInput)
    
    def clean(self):
        cleaned_data = super().clean()
        id_number = cleaned_data.get('id_number')
        password = cleaned_data.get('password')
        
        if id_number and password:
            try:
                # Find the user associated with this ID number
                profile = Profile.objects.get(id_number=id_number)
                # Then try to authenticate with their username (email)
                user = authenticate(username=profile.user.username, password=password)
                
                if not user:
                    raise forms.ValidationError("Invalid credentials. Please check your ID number and password.")
                elif not user.is_active:
                    raise forms.ValidationError("This account is inactive.")
                else:
                    # Store the authenticated user in cleaned_data
                    cleaned_data['user'] = user
            except Profile.DoesNotExist:
                raise forms.ValidationError(f"No user found with ID number: {id_number}")
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Authentication error: {str(e)}")
                raise forms.ValidationError(f"Authentication error: {str(e)}")
                
        return cleaned_data

class ProfileEditForm(forms.ModelForm):
    """
    Form for editing user profile details
    """
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control'})
    )
    role = forms.ChoiceField(
        choices=Profile.ROLE_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    department = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    research_interests = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3})
    )
    phone = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    location = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    linkedin = forms.URLField(
        required=False,
        widget=forms.URLInput(attrs={'class': 'form-control'})
    )
    scholar = forms.URLField(
        required=False,
        widget=forms.URLInput(attrs={'class': 'form-control'})
    )
    profile_image = forms.ImageField(
        required=False,
        widget=forms.FileInput(attrs={'class': 'form-control'})
    )

    class Meta:
        model = Profile
        fields = [
            'email',
            'role',
            'department',
            'research_interests',
            'phone',
            'location',
            'linkedin',
            'scholar',
            'profile_image'
        ]

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email and email != self.instance.user.email:
            if User.objects.filter(email=email).exists():
                raise forms.ValidationError("This email address is already in use.")
        return email
