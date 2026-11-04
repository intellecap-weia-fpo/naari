import graphene
import graphql_jwt
from graphene import relay
from graphene_django import DjangoObjectType
from graphene_django.filter import DjangoFilterConnectionField
from graphql_jwt.decorators import login_required
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password, check_password
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.core.mail import send_mail
from django.conf import settings

import survey.schema
from customers.graphql import tenant_api_base_url, tenant_domain


User = get_user_model()


class UserNode(DjangoObjectType):
    class Meta:
        model = User
        exclude = ('password',)
        filter_fields = {
            'username': ['exact', 'icontains', 'istartswith'],
            'email': ['exact', 'icontains'],
            'first_name': ['exact', 'icontains'],
            'last_name': ['exact', 'icontains'],
            'is_active': ['exact'],
            'is_staff': ['exact'],
        }
        interfaces = (relay.Node, )


class UserType(graphene.ObjectType):
    id = graphene.ID()
    username = graphene.String()
    email = graphene.String()
    first_name = graphene.String()
    last_name = graphene.String()
    is_active = graphene.Boolean()
    is_staff = graphene.Boolean()


class TenantContextType(graphene.ObjectType):
    name = graphene.String()
    schema_name = graphene.String()
    domain = graphene.String()
    api_base_url = graphene.String()
    graphql_url = graphene.String()


class Query(survey.schema.Query, graphene.ObjectType):
    hello = graphene.String(default_value="Hi!")
    me = graphene.Field(UserType)
    tenant_context = graphene.Field(TenantContextType)
    
    # Relay fields - Protected
    user = relay.Node.Field(UserNode)
    all_users = DjangoFilterConnectionField(UserNode)

    @login_required
    def resolve_me(self, info):
        user = info.context.user
        return user

    def resolve_tenant_context(self, info):
        tenant = getattr(info.context, "tenant", None)
        if not tenant:
            return None

        api_base_url = tenant_api_base_url(info, tenant)
        return TenantContextType(
            name=getattr(tenant, "name", None),
            schema_name=getattr(tenant, "schema_name", None),
            domain=tenant_domain(tenant),
            api_base_url=api_base_url,
            graphql_url=f"{api_base_url}/graphql/" if api_base_url else "",
        )

    @login_required
    def resolve_user(self, info, **kwargs):
        user = info.context.user
        if not user.is_staff and not user.is_superuser:
            return None
        return relay.Node.get_node_from_global_id(info, kwargs.get('id'))

    @login_required
    def resolve_all_users(self, info, **kwargs):
        user = info.context.user
        if not user.is_staff and not user.is_superuser:
            return User.objects.none()
        return User.objects.all()


class CreateAccount(graphene.Mutation):
    class Arguments:
        username = graphene.String(required=True)
        email = graphene.String(required=True)
        password = graphene.String(required=True)
        first_name = graphene.String()
        last_name = graphene.String()

    user = graphene.Field(UserType)
    success = graphene.Boolean()
    message = graphene.String()

    def mutate(self, info, username, email, password, first_name=None, last_name=None):
        if not settings.ALLOW_SIGNUP:
            return CreateAccount(user=None, success=False, message="Sign up is disabled.")
        try:
            # Check if username already exists
            if User.objects.filter(username=username).exists():
                return CreateAccount(user=None, success=False, message="Username already exists")
            
            # Check if email already exists
            if User.objects.filter(email=email).exists():
                return CreateAccount(user=None, success=False, message="Email already exists")
            
            # Create new user
            user = User.objects.create(
                username=username,
                email=email,
                password=make_password(password),
                first_name=first_name or '',
                last_name=last_name or ''
            )
            
            return CreateAccount(user=user, success=True, message="Account created successfully")
        
        except Exception as e:
            return CreateAccount(user=None, success=False, message=str(e))


class ForgotPassword(graphene.Mutation):
    class Arguments:
        email = graphene.String(required=True)

    success = graphene.Boolean()
    message = graphene.String()

    def mutate(self, info, email):
        try:
            # Check if user exists with this email
            user = User.objects.filter(email=email).first()
            
            if not user:
                # Don't reveal if email exists or not for security
                return ForgotPassword(
                    success=True, 
                    message="If an account exists with this email, a password reset link has been sent."
                )
            
            # Generate password reset token
            token = default_token_generator.make_token(user)
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            
            # Create reset URL (you may need to adjust this based on your frontend URL)
            reset_url = f"{settings.FRONTEND_URL}/reset-password/{uid}/{token}"
            
            # Send email
            subject = "Password Reset Request"
            message = f"""
            Hello {user.first_name or user.username},
            
            You requested a password reset. Click the link below to reset your password:
            
            {reset_url}
            
            If you didn't request this, please ignore this email.
            
            This link will expire in 24 hours.
            """
            
            send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=False,
            )
            
            return ForgotPassword(
                success=True,
                message="If an account exists with this email, a password reset link has been sent."
            )
            
        except Exception as e:
            return ForgotPassword(
                success=False,
                message="An error occurred while processing your request. Please try again later."
            )


class ResetPassword(graphene.Mutation):
    class Arguments:
        uid = graphene.String(required=True)
        token = graphene.String(required=True)
        new_password = graphene.String(required=True)

    success = graphene.Boolean()
    message = graphene.String()

    def mutate(self, info, uid, token, new_password):
        try:
            from django.utils.http import urlsafe_base64_decode
            
            # Decode the user ID
            try:
                uid = urlsafe_base64_decode(uid).decode()
                user = User.objects.get(pk=uid)
            except (TypeError, ValueError, OverflowError, User.DoesNotExist):
                return ResetPassword(success=False, message="Invalid reset link")
            
            # Check if token is valid
            if not default_token_generator.check_token(user, token):
                return ResetPassword(success=False, message="Invalid or expired reset link")
            
            # Set new password
            user.password = make_password(new_password)
            user.save()
            
            return ResetPassword(success=True, message="Password has been reset successfully")
            
        except Exception as e:
            return ResetPassword(
                success=False,
                message="An error occurred while resetting your password. Please try again."
            )


class ChangePassword(graphene.Mutation):
    class Arguments:
        old_password = graphene.String(required=True)
        new_password = graphene.String(required=True)

    success = graphene.Boolean()
    message = graphene.String()

    @login_required
    def mutate(self, info, old_password, new_password):
        try:
            user = info.context.user

            # Verify the old password is correct
            if not user.check_password(old_password):
                return ChangePassword(success=False, message="Current password is incorrect")

            # Validate new password length
            if len(new_password) < 8:
                return ChangePassword(success=False, message="New password must be at least 8 characters long")

            # Don't allow same password
            if old_password == new_password:
                return ChangePassword(success=False, message="New password must be different from current password")

            # Set the new password
            user.set_password(new_password)
            user.save()

            return ChangePassword(success=True, message="Password changed successfully")

        except Exception as e:
            return ChangePassword(
                success=False,
                message="An error occurred while changing your password. Please try again."
            )


class CustomObtainJSONWebToken(graphql_jwt.ObtainJSONWebToken):
    @classmethod
    def mutate(cls, root, info, **kwargs):
        # Convert username to lowercase for case-insensitive authentication
        if 'username' in kwargs:
            kwargs['username'] = kwargs['username'].lower()
        return super().mutate(root, info, **kwargs)


class Mutation(survey.schema.Mutation, graphene.ObjectType):
    sign_in = CustomObtainJSONWebToken.Field()
    verify_token = graphql_jwt.Verify.Field()
    refresh_token = graphql_jwt.Refresh.Field()
    logout = graphql_jwt.DeleteJSONWebTokenCookie.Field()
    delete_refresh_token_cookie = graphql_jwt.DeleteRefreshTokenCookie.Field()
    create_account = CreateAccount.Field()
    forgot_password = ForgotPassword.Field()
    reset_password = ResetPassword.Field()
    change_password = ChangePassword.Field()


schema = graphene.Schema(query=Query, mutation=Mutation)
