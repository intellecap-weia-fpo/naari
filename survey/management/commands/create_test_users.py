from django.core.management.base import BaseCommand
from django.contrib.auth.models import User

class Command(BaseCommand):
    help = 'Create test users for survey sharing functionality'

    def handle(self, *args, **options):
        # Create test users
        test_users = [
            {'username': 'john_doe', 'email': 'john@example.com', 'first_name': 'John', 'last_name': 'Doe'},
            {'username': 'jane_smith', 'email': 'jane@example.com', 'first_name': 'Jane', 'last_name': 'Smith'},
            {'username': 'bob_wilson', 'email': 'bob@example.com', 'first_name': 'Bob', 'last_name': 'Wilson'},
            {'username': 'alice_brown', 'email': 'alice@example.com', 'first_name': 'Alice', 'last_name': 'Brown'},
            {'username': 'charlie_davis', 'email': 'charlie@example.com', 'first_name': 'Charlie', 'last_name': 'Davis'},
        ]
        
        created_count = 0
        for user_data in test_users:
            user, created = User.objects.get_or_create(
                username=user_data['username'],
                defaults={
                    'email': user_data['email'],
                    'first_name': user_data['first_name'],
                    'last_name': user_data['last_name'],
                    'is_active': True,
                }
            )
            if created:
                user.set_unusable_password()  # Test accounts cannot log in until a password is set
                user.save()
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f'Created user: {user.username} ({user.get_full_name()})')
                )
        
        if created_count == 0:
            self.stdout.write(
                self.style.WARNING('No new users created. All test users already exist.')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f'Successfully created {created_count} test users.')
            )