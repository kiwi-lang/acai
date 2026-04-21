import { useState, useEffect, useRef, useCallback } from 'react';
import { Box, VStack, HStack, Text, Button } from '@chakra-ui/react';
import { useWebSocket } from '../contexts/WebSocketContext';

interface LogDisplayProps {
    isVisible?: boolean;
    onToggle?: () => void;
    clearOnNewRequest?: boolean;
}

const ChevronDownIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="6 9 12 15 18 9" />
    </svg>
);

const ChevronUpIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="18 15 12 9 6 15" />
    </svg>
);

const XIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <line x1="18" y1="6" x2="6" y2="18" />
        <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
);

interface LogEntry {
    line: string;
    source?: 'stdout' | 'stderr';
    actionId?: number;
    timestamp: Date;
}

const LogDisplay = ({ isVisible = true, onToggle }: LogDisplayProps) => {
    const { socket } = useWebSocket();
    const [logs, setLogs] = useState<LogEntry[]>([]);
    const [isCollapsed, setIsCollapsed] = useState(true);
    const logsEndRef = useRef<HTMLDivElement>(null);
    const autoScrollRef = useRef<boolean>(true);

    // Expose clearLogs function via ref for parent components
    const clearLogs = () => {
        setLogs([]);
    };

    // Memoize handlers to prevent duplicate listeners
    const handleStdout = useCallback((data: { id: number; line: string }) => {
        setLogs(prev => {
            const updated = [...prev, {
                line: data.line,
                source: 'stdout' as const,
                actionId: data.id,
                timestamp: new Date()
            }];
            // Keep only last 500 lines to prevent memory issues
            return updated.slice(-500);
        });
    }, []);

    const handleStderr = useCallback((data: { id: number; line: string }) => {
        setLogs(prev => {
            const updated = [...prev, {
                line: data.line,
                source: 'stderr' as const,
                actionId: data.id,
                timestamp: new Date()
            }];
            // Keep only last 500 lines to prevent memory issues
            return updated.slice(-500);
        });
    }, []);

    // Also listen for legacy 'log' events for backward compatibility
    const handleLog = useCallback((message: string) => {
        setLogs(prev => {
            const updated = [...prev, {
                line: message,
                timestamp: new Date()
            }];
            return updated.slice(-500);
        });
    }, []);

    useEffect(() => {
        if (!socket) {
            return;
        }

        // Set up WebSocket listeners
        socket.off('stdout', handleStdout);
        socket.off('stderr', handleStderr);
        socket.off('log', handleLog);
        socket.on('stdout', handleStdout);
        socket.on('stderr', handleStderr);
        socket.on('log', handleLog);

        return () => {
            if (socket) {
                socket.off('stdout', handleStdout);
                socket.off('stderr', handleStderr);
                socket.off('log', handleLog);
            }
        };
    }, [socket, handleStdout, handleStderr, handleLog]);

    const scrollContainerRef = useRef<HTMLDivElement>(null);

    // Auto-scroll to bottom when new logs arrive, but only if user is already at bottom
    useEffect(() => {
        if (!isCollapsed && logsEndRef.current && scrollContainerRef.current) {
            const container = scrollContainerRef.current;
            // Check if user is currently at bottom (within 10px threshold)
            const isAtBottom = container.scrollHeight - container.scrollTop <= container.clientHeight + 10;

            // Update the ref
            autoScrollRef.current = isAtBottom;

            // Only scroll if user is at bottom
            if (isAtBottom) {
                logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
            }
        }
    }, [logs, isCollapsed]);

    const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
        const target = e.currentTarget;
        // Check if user is at bottom (within 10px threshold)
        const isAtBottom = target.scrollHeight - target.scrollTop <= target.clientHeight + 10;
        autoScrollRef.current = isAtBottom;
    };

    // When component expands, scroll to bottom if it was already at bottom
    useEffect(() => {
        if (!isCollapsed && scrollContainerRef.current && logs.length > 0) {
            // Small delay to ensure DOM is updated
            setTimeout(() => {
                const container = scrollContainerRef.current;
                if (container) {
                    // Check current scroll position
                    const isAtBottom = container.scrollHeight - container.scrollTop <= container.clientHeight + 10;
                    autoScrollRef.current = isAtBottom;
                    // If at bottom (or very close), scroll to bottom
                    if (isAtBottom && logsEndRef.current) {
                        logsEndRef.current.scrollIntoView({ behavior: 'auto' });
                    }
                }
            }, 100);
        }
    }, [isCollapsed, logs.length]);

    if (!isVisible) {
        return null;
    }

    return (
        <Box
            w="100%"
            bg="gray.900"
            borderTop="1px solid"
            borderColor="gray.700"
            maxH={isCollapsed ? "40px" : "300px"}
            transition="max-height 0.2s ease-in-out"
            overflow="hidden"
            flexShrink={0}
        >
            <HStack
                p={2}
                borderBottom="1px solid"
                borderColor="gray.700"
                justify="space-between"
                bg="gray.800"
                cursor="pointer"
                onClick={() => {
                    setIsCollapsed(!isCollapsed);
                    if (onToggle) onToggle();
                }}
                _hover={{ bg: 'gray.750' }}
            >
                <HStack gap={2}>
                    <Text fontSize="xs" fontWeight="bold" color="gray.400" textTransform="uppercase">
                        Logs
                    </Text>
                    {logs.length > 0 && (
                        <Text fontSize="xs" color="gray.500">
                            ({logs.length})
                        </Text>
                    )}
                </HStack>
                <HStack gap={1}>
                    {!isCollapsed && (
                        <Button
                            size="xs"
                            variant="ghost"
                            colorScheme="gray"
                            onClick={(e) => {
                                e.stopPropagation();
                                clearLogs();
                            }}
                            title="Clear logs"
                        >
                            <XIcon />
                        </Button>
                    )}
                    {isCollapsed ? <ChevronUpIcon /> : <ChevronDownIcon />}
                </HStack>
            </HStack>

            {!isCollapsed && (
                <Box
                    ref={scrollContainerRef}
                    h="260px"
                    overflowY="auto"
                    p={2}
                    fontFamily="mono"
                    fontSize="xs"
                    onScroll={handleScroll}
                    css={{
                        '&::-webkit-scrollbar': {
                            width: '8px',
                        },
                        '&::-webkit-scrollbar-track': {
                            background: 'transparent',
                        },
                        '&::-webkit-scrollbar-thumb': {
                            background: '#4a5568',
                            borderRadius: '4px',
                        },
                        '&::-webkit-scrollbar-thumb:hover': {
                            background: '#718096',
                        },
                    }}
                >
                    <VStack align="stretch" gap={0}>
                        {logs.length === 0 ? (
                            <Text color="gray.500" fontStyle="italic">
                                No logs yet...
                            </Text>
                        ) : (
                            logs.map((log, index) => (
                                <HStack key={index} align="flex-start" gap={2} w="100%">
                                    {/* Metadata: action_id and source */}
                                    <HStack gap={1} flexShrink={0} fontSize="xs" color="gray.500" fontFamily="mono">
                                        {log.actionId !== undefined && (
                                            <Text color="gray.500">[ID: {log.actionId}]</Text>
                                        )}
                                        {log.source && (
                                            <Text
                                                color={log.source === 'stderr' ? 'red.400' : 'green.400'}
                                                fontWeight="semibold"
                                            >
                                                {log.source === 'stderr' ? 'stderr' : 'stdout'}
                                            </Text>
                                        )}
                                    </HStack>
                                    {/* Log line */}
                                    <Text
                                        flex={1}
                                        color={log.source === 'stderr' ? 'red.300' : 'gray.300'}
                                        whiteSpace="pre-wrap"
                                        wordBreak="break-word"
                                        lineHeight="1.5"
                                        fontFamily="mono"
                                    >
                                        {log.line}
                                    </Text>
                                </HStack>
                            ))
                        )}
                        <div ref={logsEndRef} />
                    </VStack>
                </Box>
            )}
        </Box>
    );
};

export default LogDisplay;

