from django.core.management.base import BaseCommand
from survey.models import Question, QuestionOption


class Command(BaseCommand):
    help = 'Lookup exact text for specific question IDs in the database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--question-ids',
            type=str,
            help='Comma-separated list of question IDs to lookup (e.g., "959,960,975")'
        )

    def handle(self, *args, **options):
        # Default question IDs from the asset ownership indicator
        default_question_ids = [959, 960, 963, 966, 969, 972, 975, 978]
        
        # Get question IDs from command line arguments or use defaults
        if options['question_ids']:
            try:
                question_ids = [int(id.strip()) for id in options['question_ids'].split(',')]
            except ValueError:
                self.stdout.write(self.style.ERROR('Invalid question IDs format. Use comma-separated integers.'))
                return
        else:
            question_ids = default_question_ids

        self.stdout.write(self.style.SUCCESS(f'Looking up questions for IDs: {question_ids}'))
        self.stdout.write('=' * 80)

        # Fetch questions from database
        questions = Question.objects.filter(id__in=question_ids).order_by('id')
        
        if not questions:
            self.stdout.write(self.style.WARNING('No questions found with the specified IDs.'))
            return

        # Display each question and its options
        for question in questions:
            self.stdout.write(self.style.SUCCESS(f'\nQuestion ID: {question.id}'))
            self.stdout.write(f'Text: {question.text}')
            self.stdout.write(f'Type: {question.question_type}')
            self.stdout.write(f'Required: {question.is_required}')
            
            # Show options if it's an option-based question
            if question.question_type in ['radio', 'checkbox', 'dropdown']:
                options = question.options.all().order_by('order')
                if options:
                    self.stdout.write('Options:')
                    for option in options:
                        self.stdout.write(f'  - {option.text}')
                        if option.value:
                            self.stdout.write(f'    Value: {option.value}')
                else:
                    self.stdout.write(self.style.WARNING('  No options found for this question'))
            
            self.stdout.write('-' * 40)

        # Show which question IDs were not found
        found_ids = [q.id for q in questions]
        missing_ids = [id for id in question_ids if id not in found_ids]
        if missing_ids:
            self.stdout.write(self.style.WARNING(f'\nQuestion IDs not found in database: {missing_ids}'))