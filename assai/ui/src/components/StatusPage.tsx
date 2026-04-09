import { useState, useEffect } from 'react';
import { Box, VStack, HStack, Text, Heading, Badge } from '@chakra-ui/react';
import { getStatus, listEvents } from '../services/api';
import { useAgentSocket } from '../contexts/WebSocketContext';
import type { AgentEvent, AgentStatus } from '../services/types';

const EVENT_COLORS: Record<string, string> = {
    new_requirement: 'blue',
    clarification: 'cyan',
    decision: 'green',
    contradiction: 'red',
    task_request: 'orange',
    spec_updated: 'teal',
    spec_created: 'teal',
    task_created: 'yellow',
    task_assigned: 'purple',
    context_ready: 'blue',
    task_completed: 'green',
    task_failed: 'red',
    task_needs_review: 'orange',
};

const StatusPage = () => {
    const { status: wsStatus, events: wsEvents, isConnected } = useAgentSocket();
    const [status, setStatus] = useState<AgentStatus | null>(null);
    const [events, setEvents] = useState<AgentEvent[]>([]);
    const [error, setError] = useState('');

    useEffect(() => {
        if (wsStatus) setStatus(wsStatus);
    }, [wsStatus]);

    useEffect(() => {
        if (wsEvents.length > 0) setEvents([...wsEvents].reverse());
    }, [wsEvents]);

    useEffect(() => {
        document.title = 'Status - ASSAI';
        if (!isConnected) {
            Promise.all([getStatus(), listEvents(100)])
                .then(([s, e]) => { setStatus(s); setEvents(e.reverse()); setError(''); })
                .catch(err => setError(err instanceof Error ? err.message : 'Failed to load'));
        }
    }, [isConnected]);

    return (
        <Box h="100vh" w="100%" bg="gray.900" overflowY="auto" p={6}>
            <Box maxW="4xl" mx="auto">
                <HStack justify="space-between" mb={6}>
                    <Heading size="lg" color="white">Agent Status</Heading>
                    <Badge colorScheme={isConnected ? 'green' : 'red'} fontSize="xs" variant="outline">
                        {isConnected ? 'live' : 'disconnected'}
                    </Badge>
                </HStack>

                {error && (
                    <Box p={3} bg="red.900" borderRadius="md" mb={4}>
                        <Text color="red.200" fontSize="sm">{error}</Text>
                    </Box>
                )}

                {status && (
                    <VStack gap={4} mb={8} align="stretch">
                        <Box p={4} bg="gray.800" borderRadius="lg" border="1px solid" borderColor="gray.700">
                            <Text fontWeight="semibold" color="white" mb={3}>LLM Backend</Text>
                            <HStack gap={6}>
                                <VStack align="flex-start" gap={0}>
                                    <Text fontSize="xs" color="gray.500">Backend</Text>
                                    <Text fontSize="sm" color="gray.200">{status.llm_backend}</Text>
                                </VStack>
                                <VStack align="flex-start" gap={0}>
                                    <Text fontSize="xs" color="gray.500">Endpoint</Text>
                                    <Text fontSize="sm" color="gray.200" fontFamily="mono">{status.llm_endpoint}</Text>
                                </VStack>
                                {status.conversation_turns !== undefined && (
                                    <VStack align="flex-start" gap={0}>
                                        <Text fontSize="xs" color="gray.500">Conversation turns</Text>
                                        <Text fontSize="sm" color="gray.200">{status.conversation_turns}</Text>
                                    </VStack>
                                )}
                            </HStack>
                        </Box>

                        <Box p={4} bg="gray.800" borderRadius="lg" border="1px solid" borderColor="gray.700">
                            <Text fontWeight="semibold" color="white" mb={3}>Queue</Text>
                            <HStack gap={4} flexWrap="wrap">
                                {Object.entries(status.queue).map(([name, count]) => (
                                    <VStack key={name} gap={0} align="center" minW="60px">
                                        <Text fontSize="2xl" fontWeight="bold" color="white">{count}</Text>
                                        <Text fontSize="xs" color="gray.400">{name}</Text>
                                    </VStack>
                                ))}
                            </HStack>
                        </Box>
                    </VStack>
                )}

                <Heading size="md" color="white" mb={4}>Events</Heading>
                <VStack gap={2} align="stretch">
                    {events.length === 0 ? (
                        <Text color="gray.500" textAlign="center" py={8}>No events yet</Text>
                    ) : (
                        events.map((ev, i) => (
                            <Box
                                key={i} p={3} bg="gray.800" borderRadius="md"
                                border="1px solid" borderColor="gray.700"
                            >
                                <HStack justify="space-between" mb={1}>
                                    <HStack gap={2}>
                                        <Badge colorScheme={EVENT_COLORS[ev.kind] || 'gray'} fontSize="xs">
                                            {ev.kind}
                                        </Badge>
                                        <Text fontSize="xs" color="gray.400">from {ev.source}</Text>
                                    </HStack>
                                    <Text fontSize="xs" color="gray.500">
                                        {new Date(ev.timestamp).toLocaleString()}
                                    </Text>
                                </HStack>
                                {ev.data.summary && (
                                    <Text fontSize="sm" color="gray.300" mt={1}>{ev.data.summary}</Text>
                                )}
                            </Box>
                        ))
                    )}
                </VStack>
            </Box>
        </Box>
    );
};

export default StatusPage;
