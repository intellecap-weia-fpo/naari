// Monaco Editor integration for survey calculation scripts
class MonacoEditorManager {
    constructor() {
        this.editor = null;
        this.isInitialized = false;
        this.pendingCode = null;
    }

    // Initialize Monaco editor
    async init(containerId, textareaId, initialCode = '') {
        // Check if Monaco is already loaded
        if (typeof monaco === 'undefined') {
            await this.loadMonaco();
        }

        const container = document.getElementById(containerId);
        const textarea = document.getElementById(textareaId);

        if (!container || !textarea) {
            console.error('Monaco editor container or textarea not found');
            return;
        }

        // Store initial value
        this.pendingCode = initialCode || textarea.value;

        // Clear loading placeholder
        const loadingText = document.getElementById('monaco-loading-text');
        if (loadingText) loadingText.remove();
        container.innerHTML = '';

        // Create editor
        this.editor = monaco.editor.create(container, {
            value: this.pendingCode,
            language: 'python',
            theme: 'vs-dark',
            fontSize: 14,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            automaticLayout: true,
            wordWrap: 'on',
            lineNumbers: 'on',
            roundedSelection: false,
            scrollbar: {
                vertical: 'auto',
                horizontal: 'auto'
            },
            formatOnPaste: true,
            formatOnType: true
        });

        // Sync editor content with textarea
        this.editor.onDidChangeModelContent(() => {
            textarea.value = this.editor.getValue();
        });

        this.isInitialized = true;

        // Add resize observer for responsive behavior
        this.setupResizeObserver(container);

        return this.editor;
    }

    // Load Monaco from CDN
    async loadMonaco() {
        return new Promise((resolve, reject) => {
            // Check if already loaded
            if (typeof monaco !== 'undefined') {
                resolve();
                return;
            }

            // Load Monaco loader
            const loaderScript = document.createElement('script');
            loaderScript.src = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.50.0/min/vs/loader.js';
            loaderScript.onload = () => {
                require.config({ 
                    paths: { 
                        'vs': 'https://cdn.jsdelivr.net/npm/monaco-editor@0.50.0/min/vs' 
                    } 
                });
                
                require(['vs/editor/editor.main'], () => {
                    resolve();
                }, reject);
            };
            loaderScript.onerror = reject;
            document.head.appendChild(loaderScript);
        });
    }

    // Setup resize observer for responsive editor
    setupResizeObserver(container) {
        const resizeObserver = new ResizeObserver(() => {
            if (this.editor) {
                this.editor.layout();
            }
        });
        resizeObserver.observe(container);
    }

    // Set editor value
    setValue(code) {
        if (this.editor) {
            this.editor.setValue(code);
        } else {
            this.pendingCode = code;
        }
    }

    // Get editor value
    getValue() {
        return this.editor ? this.editor.getValue() : this.pendingCode;
    }

    // Dispose editor
    dispose() {
        if (this.editor) {
            this.editor.dispose();
            this.editor = null;
        }
        this.isInitialized = false;
    }
}

// Global instance
window.monacoEditorManager = new MonacoEditorManager();

// Initialize Monaco editor when Turbo loads the page or on DOMContentLoaded
function initMonacoEditor() {
  // Check if we're on the indicator form page
  const codeTextarea = document.getElementById('id_code');
  const monacoContainer = document.getElementById('monaco-editor-container');
  
  if (codeTextarea && monacoContainer && !monacoEditorManager.isInitialized) {
    // Initialize Monaco editor with the code textarea
    monacoEditorManager.init(
      'monaco-editor-container',
      'id_code',
      codeTextarea.value
    ).catch(error => {
      console.error('Failed to initialize Monaco editor:', error);
      // Fallback: show textarea if Monaco fails
      codeTextarea.style.display = 'block';
      monacoContainer.style.display = 'none';
    });
  }
}

document.addEventListener('turbo:load', initMonacoEditor);
document.addEventListener('DOMContentLoaded', initMonacoEditor);
// Also try after a short delay in case scripts load late
setTimeout(initMonacoEditor, 500);

// Handle form submission to ensure Monaco content is synced
document.addEventListener('turbo:submit-start', function(e) {
    const codeTextarea = document.getElementById('id_code');
    if (codeTextarea && monacoEditorManager.isInitialized) {
        // Ensure latest content is synced before Turbo submits the form
        codeTextarea.value = monacoEditorManager.getValue();
    }
});