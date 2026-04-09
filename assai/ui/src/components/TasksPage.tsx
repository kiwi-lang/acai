import { useState, useEffect, useCallback } from 'react';
import {
    Box, VStack, HStack, Text, Button, Heading, Badge,
    Input, Textarea,
} from '@chakra-ui/react';
import { listTasks, createTask } from '../services/api';
import { useAgentSocket } from '../contexts/WebSocketContext';
import type { Task } from '../services/types';

const STATUS_COLORS: Record<string, string> = {
    pending: 'yellow',
    curating: 'orange',
    ready: 'blue',
    in_progress: 'cyan',
    completed: 'green',
    failed: 'red',
    review: 'purple',
};

const TaskCard = ({ task }: { task: Task }) => (
    <Box
        p={4} bg="gray.800" borderRadius="lg"
        border="1px solid" borderColor="gray.700"
        _hover={{ borderColor: 'gray.600' }}
        transition="all 0.2s"
    >
        <HStack justify="space-between" mb={2}>
            <HStack gap={2}>
                <Badge colorScheme={task.kind === 'llm_complete' ? 'teal' : 'orange'} fontSize="xs" variant="outline">
                    {task.kind}
                </Badge>
                {task.gpu === 1 && (
                    <Badge colorScheme="red" fontSize="xs" variant="outline">GPU</Badge>
                )}
                <Text fontWeight="semibold" color="white" fontSize="md">
                    {task.title}
                </Text>
            </HStack>
            <Badge colorScheme={STATUS_COLORS[task.status] || 'gray'} fontSize="xs">
                {task.status}
            </Badge>
        </HStack>

        {task.description && (
            <Text fontSize="sm" color="gray.400" mb={3} lineHeight="1.6">
                {task.description}
            </Text>
        )}

        <HStack gap={4} flexWrap="wrap">
            <Text fontSize="xs" color="gray.500">
                Priority: {task.priority}
            </Text>
            {task.assigned_to && (
                <Text fontSize="xs" color="gray.500">
                    Assigned: {task.assigned_to}
                </Text>
            )}
            {task.retries > 0 && (
                <Text fontSize="xs" color="orange.400">
                    Retries: {task.retries}/{task.max_retries}
                </Text>
            )}
            {task.created_at && (
                <Text fontSize="xs" color="gray.500">
                    {new Date(task.created_at).toLocaleString()}
                </Text>
            )}
        </HStack>

        {task.error_log && (
            <Box mt={2} p={2} bg="red.900" borderRadius="md" maxH="100px" overflowY="auto">
                <Text fontSize="xs" color="red.200" fontFamily="mono" whiteSpace="pre-wrap">
                    {task.error_log}
                </Text>
            </Box>
        )}

        {task.depends_on && (
            <Text fontSize="xs" color="gray.500" mt={2}>
                Depends on: {task.depends_on}
            </Text>
        )}
    </Box>
);

const TasksPage = () => {
    const { tasks: wsTasks, isConnected } = useAgentSocket();
    const [tasks, setTasks] = useState<Task[]>([]);
    const [filter, setFilter] = useState<string | null>(null);
    const [showCreate, setShowCreate] = useState(false);
    const [newTitle, setNewTitle] = useState('');
    const [newDesc, setNewDesc] = useState('');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        if (wsTasks.length > 0) {
            setTasks(filter ? wsTasks.filter(t => t.status === filter) : wsTasks);
            setLoading(false);
        }
    }, [wsTasks, filter]);

    useEffect(() => {
        document.title = 'Work Queue - ASSAI';
        if (!isConnected) {
            listTasks(filter ?? undefined)
                .then(data => { setTasks(data); setLoading(false); })
                .catch(() => setLoading(false));
        }
    }, [isConnected, filter]);

    const handleCreate = async () => {
        if (!newTitle.trim()) return;
        try {
            await createTask(newTitle.trim(), newDesc.trim());
            setNewTitle('');
            setNewDesc('');
            setShowCreate(false);
        } catch {
            // silently handle
        }
    };

    const statuses = ['pending', 'curating', 'ready', 'in_progress', 'completed', 'failed', 'review'];

    return (
        <Box h="100vh" w="100%" bg="gray.900" overflowY="auto" p={6}>
            <Box maxW="4xl" mx="auto">
                <HStack justify="space-between" mb={6}>
                    <Heading size="lg" color="white">Work Queue</Heading>
                    <Button
                        colorScheme="green" size="sm"
                        onClick={() => setShowCreate(!showCreate)}
                    >
                        {showCreate ? 'Cancel' : 'New Task'}
                    </Button>
                </HStack>

                {/* Create form */}
                {showCreate && (
                    <Box p={4} bg="gray.800" borderRadius="lg" mb={6} border="1px solid" borderColor="gray.700">
                        <VStack gap={3} align="stretch">
                            <Input
                                placeholder="Task title"
                                value={newTitle}
                                onChange={e => setNewTitle(e.target.value)}
                                bg="gray.700" border="none" color="white"
                                _placeholder={{ color: 'gray.400' }}
                            />
                            <Textarea
                                placeholder="Description (optional)"
                                value={newDesc}
                                onChange={e => setNewDesc(e.target.value)}
                                bg="gray.700" border="none" color="white"
                                _placeholder={{ color: 'gray.400' }}
                                rows={3}
                            />
                            <Button
                                colorScheme="green" size="sm" alignSelf="flex-end"
                                onClick={handleCreate}
                                disabled={!newTitle.trim()}
                            >
                                Create Task
                            </Button>
                        </VStack>
                    </Box>
                )}

                {/* Status filters */}
                <HStack gap={2} mb={6} flexWrap="wrap">
                    <Button
                        size="xs" variant={filter === null ? 'solid' : 'outline'}
                        colorScheme="gray" onClick={() => setFilter(null)}
                    >
                        All ({tasks.length})
                    </Button>
                    {statuses.map(s => {
                        const count = tasks.filter(t => t.status === s).length;
                        if (count === 0 && filter !== s) return null;
                        return (
                            <Button
                                key={s} size="xs"
                                variant={filter === s ? 'solid' : 'outline'}
                                colorScheme={STATUS_COLORS[s] || 'gray'}
                                onClick={() => setFilter(filter === s ? null : s)}
                            >
                                {s} ({count})
                            </Button>
                        );
                    })}
                </HStack>

                {/* Task list */}
                <VStack gap={3} align="stretch">
                    {loading ? (
                        <Text color="gray.400" textAlign="center" py={8}>Loading...</Text>
                    ) : tasks.length === 0 ? (
                        <VStack py={12} gap={3}>
                            <Text fontSize="lg" color="gray.400">No tasks in the queue</Text>
                            <Text fontSize="sm" color="gray.500">
                                Tasks are created during conversation or manually above.
                            </Text>
                        </VStack>
                    ) : (
                        tasks.map(task => <TaskCard key={task.id} task={task} />)
                    )}
                </VStack>
            </Box>
        </Box>
    );
};

export default TasksPage;
