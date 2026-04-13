import { memo } from 'react';
import { Box } from '@chakra-ui/react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math-extended';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';

interface MarkdownProps {
    content: string;
    fontSize?: string;
}

const Markdown = memo(({ content, fontSize = 'sm' }: MarkdownProps) => (
    <Box
        className="markdown-body"
        fontSize={fontSize}
        lineHeight="1.7"
        color="var(--text-primary)"
        wordBreak="break-word"
        css={{
            '& p': { marginBottom: '0.5em' },
            '& p:last-child': { marginBottom: 0 },
            '& ul, & ol': { paddingLeft: '1.5em', marginBottom: '0.5em' },
            '& li': { marginBottom: '0.2em' },
            '& li > p': { marginBottom: '0.2em' },
            '& h1': { fontSize: '1.4em', fontWeight: 700, marginTop: '0.8em', marginBottom: '0.4em', color: 'var(--text-heading)' },
            '& h2': { fontSize: '1.2em', fontWeight: 700, marginTop: '0.7em', marginBottom: '0.3em', color: 'var(--text-heading)' },
            '& h3': { fontSize: '1.05em', fontWeight: 600, marginTop: '0.6em', marginBottom: '0.3em', color: 'var(--text-heading)' },
            '& blockquote': {
                borderLeft: '3px solid var(--border-secondary)',
                paddingLeft: '0.8em',
                color: 'var(--text-tertiary)',
                marginBottom: '0.5em',
            },
            '& hr': { borderColor: 'var(--border-primary)', margin: '0.8em 0' },
            '& table': { borderCollapse: 'collapse', width: '100%', marginBottom: '0.5em', fontSize: '0.9em' },
            '& th, & td': {
                border: '1px solid var(--border-secondary)',
                padding: '0.3em 0.6em',
                textAlign: 'left',
            },
            '& th': { background: 'var(--border-primary)', fontWeight: 600 },
            '& a': { color: 'var(--text-link)', textDecoration: 'underline' },
            '& img': { maxWidth: '100%', borderRadius: '6px' },
            '& .katex-display': {
                overflowX: 'auto',
                overflowY: 'hidden',
                padding: '0.5em 0',
            },
            '& .katex': { fontSize: '1.1em' },
        }}
    >
        <ReactMarkdown
            remarkPlugins={[remarkGfm, remarkMath]}
            rehypePlugins={[rehypeKatex]}
            components={{
                code({ className, children, ...props }) {
                    const match = /language-(\w+)/.exec(className || '');
                    const codeStr = String(children).replace(/\n$/, '');
                    if (match) {
                        return (
                            <Box borderRadius="md" overflow="hidden" my={2} fontSize="0.85em">
                                <SyntaxHighlighter
                                    style={oneDark}
                                    language={match[1]}
                                    PreTag="div"
                                    customStyle={{
                                        margin: 0,
                                        borderRadius: '6px',
                                        padding: '0.8em',
                                    }}
                                >
                                    {codeStr}
                                </SyntaxHighlighter>
                            </Box>
                        );
                    }
                    return (
                        <Box
                            as="code"
                            bg="var(--bg-code-inline)"
                            color="var(--text-code)"
                            px="0.3em"
                            py="0.1em"
                            borderRadius="sm"
                            fontSize="0.9em"
                            fontFamily="mono"
                            {...props}
                        >
                            {children}
                        </Box>
                    );
                },
            }}
        >
            {content}
        </ReactMarkdown>
    </Box>
));

Markdown.displayName = 'Markdown';

export default Markdown;
