/**
 * Survey Form Validation JavaScript
 * Provides real-time client-side validation for survey forms
 */

document.addEventListener('DOMContentLoaded', function() {
    
    // Real-time validation for text inputs
    function validateTextInput(input, minLength, maxLength, pattern = null) {
        const value = input.value.trim();
        const errorDiv = input.parentNode.querySelector('.validation-error') || createErrorDiv(input);
        
        // Clear previous errors
        errorDiv.textContent = '';
        input.classList.remove('is-invalid');
        
        let isValid = true;
        let errorMessage = '';
        
        // Check minimum length
        if (minLength && value.length < minLength) {
            isValid = false;
            errorMessage = `Must be at least ${minLength} characters long`;
        }
        
        // Check maximum length
        if (maxLength && value.length > maxLength) {
            isValid = false;
            errorMessage = `Cannot exceed ${maxLength} characters`;
        }
        
        // Check pattern
        if (pattern && value && !pattern.test(value)) {
            isValid = false;
            errorMessage = 'Invalid format';
        }
        
        // Check for required field
        if (input.hasAttribute('required') && !value) {
            isValid = false;
            errorMessage = 'This field is required';
        }
        
        // Display error
        if (!isValid) {
            input.classList.add('is-invalid');
            errorDiv.textContent = errorMessage;
            errorDiv.style.display = 'block';
        } else {
            errorDiv.style.display = 'none';
        }
        
        return isValid;
    }
    
    // Create error div for displaying validation messages
    function createErrorDiv(input) {
        const errorDiv = document.createElement('div');
        errorDiv.className = 'validation-error text-danger small mt-1';
        errorDiv.style.display = 'none';
        input.parentNode.appendChild(errorDiv);
        return errorDiv;
    }
    
    // Validate survey title
    const surveyTitleInput = document.querySelector('input[name="title"]');
    if (surveyTitleInput) {
        surveyTitleInput.addEventListener('input', function() {
            validateTextInput(this, 3, 255);
        });
        
        surveyTitleInput.addEventListener('blur', function() {
            const value = this.value.trim();
            if (value && !/[a-zA-Z0-9]/.test(value)) {
                const errorDiv = this.parentNode.querySelector('.validation-error') || createErrorDiv(this);
                this.classList.add('is-invalid');
                errorDiv.textContent = 'Title must contain at least one alphanumeric character';
                errorDiv.style.display = 'block';
            }
        });
    }
    
    // Validate survey description
    const surveyDescInput = document.querySelector('textarea[name="description"]');
    if (surveyDescInput) {
        surveyDescInput.addEventListener('input', function() {
            validateTextInput(this, 0, 2000);
            updateCharacterCount(this, 2000);
        });
    }
    
    // Validate question text
    const questionTextInputs = document.querySelectorAll('textarea[name="text"]');
    questionTextInputs.forEach(input => {
        input.addEventListener('input', function() {
            validateTextInput(this, 5, 1000);
            updateCharacterCount(this, 1000);
        });
        
        input.addEventListener('blur', function() {
            const value = this.value.trim();
            if (value && !/[a-zA-Z0-9]/.test(value)) {
                const errorDiv = this.parentNode.querySelector('.validation-error') || createErrorDiv(this);
                this.classList.add('is-invalid');
                errorDiv.textContent = 'Question text must contain at least one alphanumeric character';
                errorDiv.style.display = 'block';
            }
        });
    });
    
    // Validate option text
    const optionTextInputs = document.querySelectorAll('input[name="text"]');
    optionTextInputs.forEach(input => {
        if (input.type === 'text') {
            input.addEventListener('input', function() {
                validateTextInput(this, 1, 255);
            });
        }
    });
    
    // Validate number inputs
    const numberInputs = document.querySelectorAll('input[type="number"]');
    numberInputs.forEach(input => {
        input.addEventListener('input', function() {
            const min = parseInt(this.getAttribute('min'));
            const max = parseInt(this.getAttribute('max'));
            const value = parseInt(this.value);
            const errorDiv = this.parentNode.querySelector('.validation-error') || createErrorDiv(this);
            
            errorDiv.textContent = '';
            this.classList.remove('is-invalid');
            
            if (isNaN(value)) return;
            
            if (min !== null && value < min) {
                this.classList.add('is-invalid');
                errorDiv.textContent = `Value must be at least ${min}`;
                errorDiv.style.display = 'block';
            } else if (max !== null && value > max) {
                this.classList.add('is-invalid');
                errorDiv.textContent = `Value cannot exceed ${max}`;
                errorDiv.style.display = 'block';
            } else {
                errorDiv.style.display = 'none';
            }
        });
    });
    
    // Validate email inputs
    const emailInputs = document.querySelectorAll('input[type="email"]');
    emailInputs.forEach(input => {
        input.addEventListener('input', function() {
            const emailPattern = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;
            validateTextInput(this, 0, 254, emailPattern);
        });
    });
    
    // Character count display
    function updateCharacterCount(input, maxLength) {
        let countDiv = input.parentNode.querySelector('.character-count');
        if (!countDiv) {
            countDiv = document.createElement('div');
            countDiv.className = 'character-count text-muted small mt-1';
            input.parentNode.appendChild(countDiv);
        }
        
        const currentLength = input.value.length;
        const remaining = maxLength - currentLength;
        
        countDiv.textContent = `${currentLength}/${maxLength} characters`;
        
        if (remaining < 50) {
            countDiv.classList.add('text-warning');
            countDiv.classList.remove('text-muted');
        } else if (remaining < 0) {
            countDiv.classList.add('text-danger');
            countDiv.classList.remove('text-warning', 'text-muted');
        } else {
            countDiv.classList.add('text-muted');
            countDiv.classList.remove('text-warning', 'text-danger');
        }
    }
    
    // Form submission validation
    const forms = document.querySelectorAll('form');
    forms.forEach(form => {
        form.addEventListener('submit', function(e) {
            let isFormValid = true;
            
            // Validate all required fields
            const requiredInputs = form.querySelectorAll('[required]');
            requiredInputs.forEach(input => {
                if (!input.value.trim()) {
                    e.preventDefault();
                    isFormValid = false;
                    
                    const errorDiv = input.parentNode.querySelector('.validation-error') || createErrorDiv(input);
                    input.classList.add('is-invalid');
                    errorDiv.textContent = 'This field is required';
                    errorDiv.style.display = 'block';
                }
            });
            
            // Validate specific field types
            const textInputs = form.querySelectorAll('input[type="text"], textarea');
            textInputs.forEach(input => {
                const minLength = parseInt(input.getAttribute('minlength'));
                const maxLength = parseInt(input.getAttribute('maxlength'));
                
                if (!validateTextInput(input, minLength, maxLength)) {
                    e.preventDefault();
                    isFormValid = false;
                }
            });
            
            // Validate email inputs
            const emailInputs = form.querySelectorAll('input[type="email"]');
            emailInputs.forEach(input => {
                const emailPattern = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;
                if (input.value && !emailPattern.test(input.value)) {
                    e.preventDefault();
                    isFormValid = false;
                    
                    const errorDiv = input.parentNode.querySelector('.validation-error') || createErrorDiv(input);
                    input.classList.add('is-invalid');
                    errorDiv.textContent = 'Please enter a valid email address';
                    errorDiv.style.display = 'block';
                }
            });
            
            if (!isFormValid) {
                // Scroll to first error
                const firstError = form.querySelector('.is-invalid');
                if (firstError) {
                    firstError.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    firstError.focus();
                }
            }
        });
    });
    
    // Real-time duplicate option validation
    function validateDuplicateOptions(questionContainer) {
        const optionInputs = questionContainer.querySelectorAll('.option-text-input');
        const values = [];
        
        optionInputs.forEach((input, index) => {
            const value = input.value.trim().toLowerCase();
            const errorDiv = input.parentNode.querySelector('.validation-error') || createErrorDiv(input);
            
            // Clear previous duplicate error
            if (errorDiv.textContent === 'This option already exists for this question.') {
                errorDiv.style.display = 'none';
                input.classList.remove('is-invalid');
            }
            
            if (value && values.includes(value)) {
                input.classList.add('is-invalid');
                errorDiv.textContent = 'This option already exists for this question.';
                errorDiv.style.display = 'block';
            } else if (value) {
                values.push(value);
            }
        });
    }
    
    // Add duplicate validation for option inputs
    const optionContainers = document.querySelectorAll('.question-options-container');
    optionContainers.forEach(container => {
        const optionInputs = container.querySelectorAll('.option-text-input');
        optionInputs.forEach(input => {
            input.addEventListener('input', function() {
                setTimeout(() => validateDuplicateOptions(container), 100);
            });
        });
    });
    
    // Add visual feedback for form validation
    const style = document.createElement('style');
    style.textContent = `
        .is-invalid {
            border-color: #dc3545 !important;
            box-shadow: 0 0 0 0.2rem rgba(220, 53, 69, 0.25) !important;
        }
        
        .validation-error {
            font-size: 0.875rem;
            margin-top: 0.25rem;
        }
        
        .character-count {
            text-align: right;
            font-size: 0.75rem;
        }
        
        .form-control:valid {
            border-color: #28a745;
        }
        
        .form-control:valid:focus {
            border-color: #28a745;
            box-shadow: 0 0 0 0.2rem rgba(40, 167, 69, 0.25);
        }
    `;
    document.head.appendChild(style);
    
    console.log('Survey validation JavaScript loaded successfully');
});