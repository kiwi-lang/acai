import { useRef, useCallback } from 'react';
import Editor, { type OnMount, type OnChange } from '@monaco-editor/react';
import type { Monaco } from '@monaco-editor/react';
import { useColorMode } from './ui/color-mode';

export interface CodeEditorProps {
    value: string;
    onChange?: (value: string) => void;
    language?: string;
    readOnly?: boolean;
    /** Minimum height in px — the editor fills its parent by default. */
    minHeight?: string;
    placeholder?: string;
}

let _jinjaRegistered = false;

function registerJinja(monaco: Monaco) {
    if (_jinjaRegistered) return;
    _jinjaRegistered = true;

    monaco.languages.register({ id: 'jinja' });

    monaco.languages.setMonarchTokensProvider('jinja', {
        tokenizer: {
            root: [
                [/\{#/, 'comment.jinja', '@jinjaComment'],
                [/\{%-?\s*/, 'delimiter.jinja', '@jinjaTag'],
                [/\{\{-?\s*/, 'delimiter.jinja', '@jinjaExpr'],
                [/./, 'text'],
            ],

            jinjaComment: [
                [/#\}/, 'comment.jinja', '@pop'],
                [/./, 'comment.jinja'],
            ],

            jinjaTag: [
                [/-?%\}/, 'delimiter.jinja', '@pop'],
                [/\b(if|else|elif|endif|for|endfor|block|endblock|extends|include|import|from|macro|endmacro|call|endcall|filter|endfilter|set|raw|endraw|with|endwith|autoescape|endautoescape|trans|endtrans|do|continue|break|pluralize|scoped|as|recursive|not|and|or|in|is|true|false|none|loop|self|varargs|kwargs|caller)\b/, 'keyword.jinja'],
                [/"[^"]*"/, 'string.jinja'],
                [/'[^']*'/, 'string.jinja'],
                [/\|/, 'operator.jinja'],
                [/\b\d+(\.\d+)?\b/, 'number.jinja'],
                [/[a-zA-Z_]\w*/, 'variable.jinja'],
                [/[()[\].,=~!<>+\-*/%]/, 'operator.jinja'],
                [/\s+/, 'white'],
            ],

            jinjaExpr: [
                [/-?\}\}/, 'delimiter.jinja', '@pop'],
                [/\|/, 'operator.jinja'],
                [/"[^"]*"/, 'string.jinja'],
                [/'[^']*'/, 'string.jinja'],
                [/\b(true|false|none|not|and|or|in|is|if|else)\b/, 'keyword.jinja'],
                [/\b\d+(\.\d+)?\b/, 'number.jinja'],
                [/[a-zA-Z_]\w*/, 'variable.jinja'],
                [/[()[\].,=~!<>+\-*/%]/, 'operator.jinja'],
                [/\s+/, 'white'],
            ],
        },
    });

    monaco.languages.setLanguageConfiguration('jinja', {
        comments: { blockComment: ['{#', '#}'] },
        brackets: [
            ['{%', '%}'],
            ['{{', '}}'],
            ['{#', '#}'],
            ['(', ')'],
            ['[', ']'],
        ],
        autoClosingPairs: [
            { open: '{{', close: ' }}' },
            { open: '{%', close: ' %}' },
            { open: '{#', close: ' #}' },
            { open: '"', close: '"' },
            { open: "'", close: "'" },
            { open: '(', close: ')' },
            { open: '[', close: ']' },
        ],
        surroundingPairs: [
            { open: '"', close: '"' },
            { open: "'", close: "'" },
            { open: '(', close: ')' },
            { open: '[', close: ']' },
        ],
    });
}

function defineThemes(monaco: Monaco, colorMode: string) {
    monaco.editor.defineTheme('acai-dark', {
        base: 'vs-dark',
        inherit: true,
        rules: [
            { token: 'delimiter.jinja', foreground: 'e5c07b', fontStyle: 'bold' },
            { token: 'keyword.jinja', foreground: 'c678dd' },
            { token: 'variable.jinja', foreground: '61afef' },
            { token: 'string.jinja', foreground: '98c379' },
            { token: 'number.jinja', foreground: 'd19a66' },
            { token: 'operator.jinja', foreground: '56b6c2' },
            { token: 'comment.jinja', foreground: '5c6370', fontStyle: 'italic' },
            { token: 'text', foreground: 'abb2bf' },
        ],
        colors: {
            'editor.background': '#1e1e1e',
            'editor.foreground': '#d4d4d4',
        },
    });

    monaco.editor.defineTheme('acai-light', {
        base: 'vs',
        inherit: true,
        rules: [
            { token: 'delimiter.jinja', foreground: '986801', fontStyle: 'bold' },
            { token: 'keyword.jinja', foreground: 'a626a4' },
            { token: 'variable.jinja', foreground: '4078f2' },
            { token: 'string.jinja', foreground: '50a14f' },
            { token: 'number.jinja', foreground: '986801' },
            { token: 'operator.jinja', foreground: '0184bc' },
            { token: 'comment.jinja', foreground: 'a0a1a7', fontStyle: 'italic' },
            { token: 'text', foreground: '383a42' },
        ],
        colors: {
            'editor.background': '#f8f9fa',
            'editor.foreground': '#1a202c',
        },
    });

    monaco.editor.setTheme(colorMode === 'dark' ? 'acai-dark' : 'acai-light');
}

const CodeEditor = ({
    value,
    onChange,
    language = 'python',
    readOnly = false,
    minHeight = '200px',
}: CodeEditorProps) => {
    const { colorMode } = useColorMode();
    const editorRef = useRef<Parameters<OnMount>[0] | null>(null);

    const handleMount: OnMount = useCallback((editor, monaco) => {
        editorRef.current = editor;
        registerJinja(monaco);
        defineThemes(monaco, colorMode);
    }, [colorMode]);

    const handleChange: OnChange = useCallback((val) => {
        if (onChange && val !== undefined) {
            onChange(val);
        }
    }, [onChange]);

    const theme = colorMode === 'dark' ? 'acai-dark' : 'acai-light';

    return (
        <div style={{ width: '100%', height: '100%', minHeight, borderRadius: '6px', overflow: 'hidden' }}>
            <Editor
                value={value}
                onChange={handleChange}
                language={language}
                theme={theme}
                onMount={handleMount}
                options={{
                    readOnly,
                    minimap: { enabled: false },
                    fontSize: 13,
                    lineNumbers: 'on',
                    scrollBeyondLastLine: false,
                    wordWrap: 'on',
                    automaticLayout: true,
                    padding: { top: 12, bottom: 12 },
                    renderLineHighlight: 'gutter',
                    scrollbar: {
                        verticalScrollbarSize: 8,
                        horizontalScrollbarSize: 8,
                    },
                    overviewRulerLanes: 0,
                    hideCursorInOverviewRuler: true,
                    overviewRulerBorder: false,
                    folding: true,
                    tabSize: 4,
                    insertSpaces: true,
                    bracketPairColorization: { enabled: true },
                }}
            />
        </div>
    );
};

export default CodeEditor;
