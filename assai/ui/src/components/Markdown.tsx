import { memo } from 'react';
import { Box } from '@chakra-ui/react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
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
        color="gray.200"
        wordBreak="break-word"
        css={{
            '& p': { marginBottom: '0.5em' },
            '& p:last-child': { marginBottom: 0 },
            '& ul, & ol': { paddingLeft: '1.5em', marginBottom: '0.5em' },
            '& li': { marginBottom: '0.2em' },
            '& li > p': { marginBottom: '0.2em' },
            '& h1': { fontSize: '1.4em', fontWeight: 700, marginTop: '0.8em', marginBottom: '0.4em', color: 'white' },
            '& h2': { fontSize: '1.2em', fontWeight: 700, marginTop: '0.7em', marginBottom: '0.3em', color: 'white' },
            '& h3': { fontSize: '1.05em', fontWeight: 600, marginTop: '0.6em', marginBottom: '0.3em', color: 'white' },
            '& blockquote': {
                borderLeft: '3px solid var(--chakra-colors-gray-600)',
                paddingLeft: '0.8em',
                color: 'var(--chakra-colors-gray-400)',
                marginBottom: '0.5em',
            },
            '& hr': { borderColor: 'var(--chakra-colors-gray-700)', margin: '0.8em 0' },
            '& table': { borderCollapse: 'collapse', width: '100%', marginBottom: '0.5em', fontSize: '0.9em' },
            '& th, & td': {
                border: '1px solid var(--chakra-colors-gray-600)',
                padding: '0.3em 0.6em',
                textAlign: 'left',
            },
            '& th': { background: 'var(--chakra-colors-gray-700)', fontWeight: 600 },
            '& a': { color: 'var(--chakra-colors-blue-300)', textDecoration: 'underline' },
            '& img': { maxWidth: '100%', borderRadius: '6px' },
        }}
    >
        <ReactMarkdown
            remarkPlugins={[remarkGfm]}
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
                            bg="gray.700"
                            color="green.200"
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
