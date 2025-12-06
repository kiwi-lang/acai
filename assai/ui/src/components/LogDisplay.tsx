import { useState, useEffect, useRef } from 'react';
import { Box, VStack, HStack, Text, IconButton, Button } from '@chakra-ui/react';
import { websocketService } from '../services/websocket';

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

const LogDisplay = ({ isVisible = true, onToggle, clearOnNewRequest = false }: LogDisplayProps) => {
    const [logs, setLogs] = useState<string[]>([]);
    const [isCollapsed, setIsCollapsed] = useState(false);
    const logsEndRef = useRef<HTMLDivElement>(null);
    const autoScrollRef = useRef<boolean>(true);
    const prevRequestIdRef = useRef<string | null>(null);

    // Expose clearLogs function via ref for parent components
    const clearLogs = () => {
        setLogs([]);
    };

    useEffect(() => {
        // Listen for log events from WebSocket
        const handleLog = (message: string) => {
            setLogs(prev => {
                const updated = [...prev, message];
                // Keep only last 500 lines to prevent memory issues
                return updated.slice(-500);
            });
        };

        // Ensure WebSocket is connected
        websocketService.connect();

        // Set up WebSocket listener for 'log' events
        const setupListener = () => {
            const socket = websocketService.getSocket();
            if (socket && socket.connected) {
                socket.on('log', handleLog);
                return true;
            }
            return false;
        };

        // Try to set up listener immediately
        if (!setupListener()) {
            // If socket is not ready yet, wait for connection
            const checkConnection = setInterval(() => {
                if (setupListener()) {
                    clearInterval(checkConnection);
                }
            }, 100);

            // Also listen for connect event
            const socket = websocketService.getSocket();
            if (socket) {
                const onConnect = () => {
                    setupListener();
                };
                socket.on('connect', onConnect);

                return () => {
                    clearInterval(checkConnection);
                    socket.off('connect', onConnect);
                    socket.off('log', handleLog);
                };
            }

            return () => {
                clearInterval(checkConnection);
                const socket = websocketService.getSocket();
                if (socket) {
                    socket.off('log', handleLog);
                }
            };
        }

        return () => {
            const socket = websocketService.getSocket();
            if (socket) {
                socket.off('log', handleLog);
            }
        };
    }, []);

    // Auto-scroll to bottom when new logs arrive
    useEffect(() => {
        if (autoScrollRef.current && logsEndRef.current && !isCollapsed) {
            logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
        }
    }, [logs, isCollapsed]);

    const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
        const target = e.currentTarget;
        const isAtBottom = target.scrollHeight - target.scrollTop <= target.clientHeight + 10;
        autoScrollRef.current = isAtBottom;
    };

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
                                <Text
                                    key={index}
                                    color="gray.300"
                                    whiteSpace="pre-wrap"
                                    wordBreak="break-word"
                                    lineHeight="1.5"
                                >
                                    {log}
                                </Text>
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

