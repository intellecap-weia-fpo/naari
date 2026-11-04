from django.core.management.base import BaseCommand
from survey.models import Submission, Indicator, IndicatorResult


class Command(BaseCommand):
    help = 'Recalculate indicators for specific submissions'

    def add_arguments(self, parser):
        parser.add_argument(
            '--submission-id',
            type=int,
            help='Submission ID to recalculate indicators for'
        )
        parser.add_argument(
            '--indicator-name',
            type=str,
            help='Name of the indicator to recalculate (partial match)'
        )

    def handle(self, *args, **options):
        submission_id = options.get('submission_id')
        indicator_name = options.get('indicator_name')
        
        if not submission_id:
            self.stdout.write(self.style.ERROR('Please provide a submission ID with --submission-id'))
            return
        
        try:
            submission = Submission.objects.get(id=submission_id)
            self.stdout.write(self.style.SUCCESS(f'Recalculating indicators for submission {submission.id}'))
            self.stdout.write(f'Survey: {submission.survey.title}')
            self.stdout.write('=' * 80)
        except Submission.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Submission {submission_id} not found'))
            return
        
        # Get indicators to recalculate
        if indicator_name:
            indicators = Indicator.objects.filter(survey=submission.survey, name__icontains=indicator_name)
        else:
            indicators = Indicator.objects.filter(survey=submission.survey)
        
        if not indicators:
            self.stdout.write(self.style.WARNING('No indicators found'))
            return
        
        for indicator in indicators:
            self.stdout.write(f'\nProcessing indicator: {indicator.name}')
            
            # Get existing result
            existing_result = IndicatorResult.objects.filter(indicator=indicator, submission=submission).first()
            if existing_result:
                self.stdout.write(f'  Existing result: {existing_result.result}')
            else:
                self.stdout.write('  No existing result')
            
            # Execute the indicator
            execution_result = indicator.execute(submission)
            
            if execution_result['success']:
                # Update or create result
                if existing_result:
                    existing_result.result = execution_result['result']
                    existing_result.success = True
                    existing_result.error_message = None
                    existing_result.save()
                    self.stdout.write(self.style.SUCCESS(f'  ✓ Updated result to: {execution_result["result"]}'))
                else:
                    IndicatorResult.objects.create(
                        indicator=indicator,
                        submission=submission,
                        result=execution_result['result'],
                        success=True
                    )
                    self.stdout.write(self.style.SUCCESS(f'  ✓ Created result: {execution_result["result"]}'))
            else:
                self.stdout.write(self.style.ERROR(f'  ✗ Execution failed: {execution_result["error"]}'))
                if existing_result:
                    existing_result.success = False
                    existing_result.error_message = execution_result['error']
                    existing_result.save()
        
        self.stdout.write(self.style.SUCCESS('\nRecalculation completed'))