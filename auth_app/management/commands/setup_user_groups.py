from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, User


class Command(BaseCommand):
    help = 'Create user groups and assign users to groups'

    def add_arguments(self, parser):
        parser.add_argument('--username', type=str, help='Username to assign to cohortmanager group')
        parser.add_argument('--list-users', action='store_true', help='List all users and their groups')

    def handle(self, *args, **options):
        # Create groups if they don't exist
        groups = ['admin', 'cohortmanager', 'startupadmin', 'startupuser']
        for group_name in groups:
            group, created = Group.objects.get_or_create(name=group_name)
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created group: {group_name}'))
            else:
                self.stdout.write(f'Group already exists: {group_name}')

        # List users and their groups
        if options['list_users']:
            self.stdout.write('\nUsers and their groups:')
            for user in User.objects.all():
                groups = ', '.join(user.groups.values_list('name', flat=True)) or 'No groups'
                self.stdout.write(f'  {user.username}: {groups}')

        # Assign user to cohortmanager group
        if options['username']:
            try:
                user = User.objects.get(username=options['username'])
                cohortmanager_group = Group.objects.get(name='cohortmanager')
                
                if cohortmanager_group not in user.groups.all():
                    user.groups.add(cohortmanager_group)
                    self.stdout.write(
                        self.style.SUCCESS(f'Added user {user.username} to cohortmanager group')
                    )
                else:
                    self.stdout.write(f'User {user.username} is already in cohortmanager group')
                    
            except User.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f'User {options["username"]} does not exist')
                )