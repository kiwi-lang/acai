import { useEffect, useRef, useState } from 'react';
import { Box, Text } from '@chakra-ui/react';
import mermaid from 'mermaid';

interface MermaidTreemapProps {
    definition: string;
    id: string;
}

const MermaidTreemap = ({ definition, id }: MermaidTreemapProps) => {
    const ref = useRef<HTMLDivElement>(null);
    const [error, setError] = useState<string | null>(null);
    const [svg, setSvg] = useState<string | null>(null);
    const renderIdRef = useRef<string>(`mermaid-treemap-${id}`);

    useEffect(() => {
        if (!definition || definition.trim() === '') {
            setSvg(null);
            setError(null);
            return;
        }

        // Initialize Mermaid (safe to call multiple times)
        mermaid.initialize({
            startOnLoad: false,
            theme: 'dark',
            securityLevel: 'loose',
            // @ts-ignore - treemap config not in types yet
            treemap: {
                valueFormat: ',.2s',
                showValues: true,
                padding: 5,
            },
        });

        // Clear previous state
        setError(null);
        setSvg(null);

        // Create a unique ID for this render
        const renderId = `${renderIdRef.current}-${Date.now()}`;
        let cancelled = false;

        // Use mermaid.render to get SVG directly
        mermaid.render(renderId, definition)
            .then((result) => {
                if (!cancelled) {
                    // Don't modify the SVG - let Mermaid handle the rendering properly
                    // CSS will handle the responsive sizing
                    setSvg(result.svg);
                }
            })
            .catch((err) => {
                if (!cancelled) {
                    console.error('Error rendering Mermaid treemap:', err);
                    console.error('Definition:', definition);
                    setError(err.message || 'Failed to render treemap');
                }
            });

        // Cleanup function
        return () => {
            cancelled = true;
        };
    }, [definition, id]);

    if (!definition || definition.trim() === '') {
        return null;
    }

    if (error) {
        return (
            <Box w="100%">
                <Text color="red.400" fontSize="sm" mb={2}>
                    Error rendering treemap: {error}
                </Text>
                <Box
                    as="pre"
                    color="gray.400"
                    fontSize="xs"
                    fontFamily="mono"
                    overflow="auto"
                    p={2}
                    bg="gray.900"
                    borderRadius="md"
                    whiteSpace="pre-wrap"
                >
                    {definition}
                </Box>
            </Box>
        );
    }

    return (
        <Box
            w="100%"
            h="100%"
            overflow="auto"
            className="HERE"
        >
            {svg ? (
                <Box
                    ref={ref}
                    dangerouslySetInnerHTML={{ __html: svg }}
                    w="100%"
                    h="100%"
                    css={{
                        '& svg': {
                            width: '100%',
                            display: 'block'
                        },
                    }}
                />
            ) : (
                <Text color="gray.400" fontSize="sm">
                    Loading treemap...
                </Text>
            )}
        </Box>
    );
};

export default MermaidTreemap;

