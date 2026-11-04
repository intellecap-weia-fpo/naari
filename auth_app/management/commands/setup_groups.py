from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, User
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType


class Command(BaseCommand):
    help = 'Setup user groups with specific permissions'

    def handle(self, *args, **options):
        # Create groups
        groups = ['admin', 'cohortmanager', 'startupadmin', 'startupuser']
        
        for group_name in groups:
            group, created = Group.objects.get_or_create(name=group_name)
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created group: {group_name}'))
            else:
                self.stdout.write(self.style.WARNING(f'Group already exists: {group_name}'))
            
            # Assign permissions based on group
            self.assign_permissions(group)
        
        self.stdout.write(self.style.SUCCESS('All groups have been set up successfully!'))
        
        # Create test users for each group (optional)
        if options.get('create_test_users', False):
            self.create_test_users()
    
    def assign_permissions(self, group):
        """Assign specific permissions to each group"""
        
        if group.name == 'admin':
            # Admin gets all permissions
            permissions = Permission.objects.all()
            group.permissions.set(permissions)
            
        elif group.name == 'cohortmanager':
            # Cohort manager gets limited permissions
            # Add permissions for managing cohorts and courses
            permission_codenames = [
                'add_cohort', 'change_cohort', 'delete_cohort', 'view_cohort',
                'add_course', 'change_course', 'delete_course', 'view_course',
                'view_startup', 'view_certificatetemplate', 'view_mentor',
            ]
            permissions = Permission.objects.filter(codename__in=permission_codenames)
            group.permissions.set(permissions)
            
        elif group.name == 'startupadmin':
            # Startup admin can manage their own startup
            permission_codenames = [
                'change_startup', 'view_startup',
                'add_businessmodelcanvas', 'change_businessmodelcanvas', 
                'delete_businessmodelcanvas', 'view_businessmodelcanvas',
            ]
            permissions = Permission.objects.filter(codename__in=permission_codenames)
            group.permissions.set(permissions)
            
        elif group.name == 'startupuser':
            # Startup user can only view
            permission_codenames = [
                'view_startup', 'view_businessmodelcanvas',
            ]
            permissions = Permission.objects.filter(codename__in=permission_codenames)
            group.permissions.set(permissions)
    
    def create_test_users(self):
        """Create test users for each group"""
        test_users = [
            {'username': 'admin1', 'email': 'admin@example.com', 'group': 'admin'},
            {'username': 'cohortmanager1', 'email': 'cohortmanager@example.com', 'group': 'cohortmanager'},
            {'username': 'startupadmin1', 'email': 'startupadmin@example.com', 'group': 'startupadmin'},
            {'username': 'startupuser1', 'email': 'startupuser@example.com', 'group': 'startupuser'},
        ]
        
        for user_data in test_users:
            user, created = User.objects.get_or_create(
                username=user_data['username'],
                defaults={
                    'email': user_data['email'],
                    'is_active': True,
                }
            )
            
            if created:
                user.set_unusable_password()  # Demo accounts cannot log in until a password is set
                user.save()
                self.stdout.write(self.style.SUCCESS(f'Created user: {user.username}'))
            
            # Add user to group
            group = Group.objects.get(name=user_data['group'])
            user.groups.add(group)
            self.stdout.write(self.style.SUCCESS(f'Added {user.username} to {group.name} group'))
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--create-test-users',
            action='store_true',
            help='Create test users for each group',
        )