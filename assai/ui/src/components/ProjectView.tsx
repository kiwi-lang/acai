import { useState, useEffect, useRef, KeyboardEvent } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
    Box, VStack, HStack, Text, Heading, Badge, Textarea, IconButton, Spinner,
} from '@chakra-ui/react';
import { getProject, converse, getHistory, listTasks, createTask, updateTask } from '../services/api';
import { useAgentSocket } from '../contexts/WebSocketContext';
import type { Project, Task, AgentMessage } from '../services/types';

const SendIcon = () => (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
        <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
    </svg>
);

const BackIcon = () => (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="15 18 9 12 15 6" />
    </svg>
);

const STATUS_COLORS: Record<string, string> = {
    pending: 'yellow',
    curating: 'orange',
    ready: 'blue',
    in_progress: 'cyan',
    completed: 'green',
    failed: 'red',
    review: 'purple',
};

const COLUMNS = [
    { key: 'pending', label: 'Pending' },
    { key: 'ready', label: 'Ready' },
    { key: 'in_progress', label: 'In Progress' },
    { key: 'review', label: 'Review' },
    { key: 'completed', label: 'Done' },
    { key: 'failed', label: 'Failed' },
];

const KanbanCard = ({ task }: { task: Task }) => (
    <Box
        p={3} bg="gray.700" borderRadius="md"
        border="1px solid" borderColor="gray.600"
        _hover={{ borderColor: 'gray.500', bg: 'gray.650' }}
        transition="all 0.15s"
        cursor="default"
    >
        <HStack justify="space-between" mb={1}>
            <Text fontSize="sm" fontWeight="medium" color="white" lineClamp={2}>
                {task.title}
            </Text>
            <Badge
                colorScheme={task.kind === 'llm_complete' ? 'teal' : 'orange'}
                fontSize="2xs" variant="outline" flexShrink={0}
            >
                {task.kind === 'llm_complete' ? 'LLM' : 'Tool'}
            </Badge>
        </HStack>
        {task.description && (
            <Text fontSize="xs" color="gray.400" lineClamp={2} mb={1}>
                {task.description}
            </Text>
        )}
        <HStack gap={2} mt={1}>
            {task.gpu === 1 && (
                <Badge colorScheme="red" fontSize="2xs" variant="outline">GPU</Badge>
            )}
            <Text fontSize="2xs" color="gray.500">P{task.priority}</Text>
            {task.retries > 0 && (
                <Text fontSize="2xs" color="orange.400">
                    retry {task.retries}/{task.max_retries}
                </Text>
            )}
        </HStack>
    </Box>
);

const KanbanColumn = ({ label, tasks, color }: { label: string; tasks: Task[]; color: string }) => (
    <Box flex={1} minW="180px" maxW="280px">
        <HStack mb={3} gap={2}>
            <Box w="8px" h="8px" borderRadius="full" bg={`${color}.400`} />
            <Text fontSize="sm" fontWeight="semibold" color="gray.300">{label}</Text>
            <Badge colorScheme={color} fontSize="2xs" variant="subtle">{tasks.length}</Badge>
        </HStack>
        <VStack
            gap={2} align="stretch"
            bg="gray.850" borderRadius="lg" p={2}
            minH="120px" maxH="calc(100vh - 180px)" overflowY="auto"
            border="1px solid" borderColor="gray.700"
        >
            {tasks.length === 0 ? (
                <Text fontSize="xs" color="gray.600" textAlign="center" py={4}>Empty</Text>
            ) : (
                tasks.map(t => <KanbanCard key={t.id} task={t} />)
            )}
        </VStack>
    </Box>
);

const ProjectView = () => {
    const { name } = useParams<{ name: string }>();
    const navigate = useNavigate();
    const { tasks: wsTasks, isConnected } = useAgentSocket();

    const [project, setProject] = useState<Project | null>(null);
    const [tasks, setTasks] = useState<Task[]>([]);
    const [messages, setMessages] = useState<AgentMessage[]>([]);
    const [input, setInput] = useState('');
    const [isLoading, setIsLoading] = useState(false);

    const messagesEndRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    useEffect(() => {
        if (!name) return;
        document.title = `${name} - ASSAI`;
        getProject(name).then(setProject).catch(() => navigate('/projects'));
        getHistory().then(setMessages).catch(() => {});
    }, [name, navigate]);

    useEffect(() => {
        if (wsTasks.length > 0) {
            setTasks(wsTasks);
        } else if (!isConnected) {
            listTasks().then(setTasks).catch(() => {});
        }
    }, [wsTasks, isConnected]);

    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }, [messages]);

    const handleSend = async () => {
        const text = input.trim();
        if (!text || isLoading) return;

        setInput('');
        if (textareaRef.current) textareaRef.current.style.height = 'auto';

        setMessages(prev => [...prev, { role: 'user', content: text }]);
        setIsLoading(true);

        try {
            const response = await converse(text);
            setMessages(prev => [...prev, { role: 'assistant', content: response }]);
        } catch (err) {
            const msg = err instanceof Error ? err.message : 'Request failed';
            setMessages(prev => [...prev, { role: 'assistant', content: `Error: ${msg}` }]);
        } finally {
            setIsLoading(false);
        }
    };

    const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
        setInput(e.target.value);
        e.target.style.height = 'auto';
        e.target.style.height = Math.min(e.target.scrollHeight, 160) + 'px';
    };

    if (!project) {
        return (
            <Box h="100vh" w="100%" bg="gray.900" display="flex" alignItems="center" justifyContent="center">
                <Spinner color="green.300" size="lg" />
            </Box>
        );
    }

    return (
        <Box h="100vh" w="100%" bg="gray.900" display="flex" flexDirection="column" overflow="hidden">
            {/* Header */}
            <HStack
                px={4} py={3}
                borderBottom="1px solid" borderColor="gray.700"
                bg="gray.900" flexShrink={0}
            >
                <IconButton
                    aria-label="Back to projects"
                    onClick={() => navigate('/projects')}
                    variant="ghost" size="sm" color="gray.400"
                    _hover={{ color: 'white' }}
                >
                    <BackIcon />
                </IconButton>
                <HStack gap={2} flex={1}>
                    <Heading size="md" color="white">{project.name}</Heading>
                    <Badge colorScheme="blue" fontSize="xs" variant="outline">{project.language}</Badge>
                    <Badge colorScheme={project.source === 'clone' ? 'purple' : 'green'} fontSize="xs">
                        {project.source}
                    </Badge>
                </HStack>
                <Text fontSize="xs" color="gray.500" fontFamily="mono">{project.path}</Text>
            </HStack>

            {/* Main: Kanban + Chat */}
            <HStack flex={1} minH={0} align="stretch" gap={0}>
                {/* Kanban Board */}
                <Box flex={1} overflowX="auto" overflowY="hidden" p={4}>
                    <HStack gap={3} align="flex-start" h="100%" minW="min-content">
                        {COLUMNS.map(col => (
                            <KanbanColumn
                                key={col.key}
                                label={col.label}
                                color={STATUS_COLORS[col.key] || 'gray'}
                                tasks={tasks.filter(t => t.status === col.key)}
                            />
                        ))}
                    </HStack>
                </Box>

                {/* Chat Panel */}
                <Box
                    w="380px" flexShrink={0}
                    borderLeft="1px solid" borderColor="gray.700"
                    display="flex" flexDirection="column"
                    bg="gray.900"
                >
                    <Box px={4} py={3} borderBottom="1px solid" borderColor="gray.700">
                        <Text fontSize="sm" fontWeight="semibold" color="gray.300">Chat</Text>
                    </Box>

                    {/* Messages */}
                    <Box flex={1} overflowY="auto" px={3} py={2} minH={0}>
                        <VStack gap={2} align="stretch">
                            {messages.length === 0 && (
                                <Text fontSize="xs" color="gray.500" textAlign="center" py={6}>
                                    Chat with the agent to create tasks, update the project, or ask questions.
                                </Text>
                            )}
                            {messages.map((msg, i) => (
                                <Box
                                    key={i}
                                    alignSelf={msg.role === 'user' ? 'flex-end' : 'flex-start'}
                                    maxW="90%"
                                    p={2.5}
                                    borderRadius="lg"
                                    bg={msg.role === 'user' ? 'purple.800' : 'gray.800'}
                                    border="1px solid"
                                    borderColor={msg.role === 'user' ? 'purple.700' : 'gray.700'}
                                >
                                    <Text fontSize="xs" color="gray.400" mb={0.5}>
                                        {msg.role === 'user' ? 'You' : 'Agent'}
                                    </Text>
                                    <Text fontSize="sm" color="gray.200" whiteSpace="pre-wrap" wordBreak="break-word" lineHeight="1.5">
                                        {msg.content}
                                    </Text>
                                </Box>
                            ))}
                            {isLoading && (
                                <HStack alignSelf="flex-start" gap={2} p={2}>
                                    <Spinner size="xs" color="green.300" />
                                    <Text fontSize="xs" color="gray.400">Thinking...</Text>
                                </HStack>
                            )}
                            <div ref={messagesEndRef} />
                        </VStack>
                    </Box>

                    {/* Input */}
                    <Box px={3} py={2} borderTop="1px solid" borderColor="gray.700">
                        <HStack gap={2} align="flex-end">
                            <Textarea
                                ref={textareaRef}
                                value={input}
                                onChange={handleChange}
                                onKeyDown={handleKeyDown}
                                placeholder="Ask or instruct..."
                                disabled={isLoading}
                                rows={1}
                                resize="none"
                                bg="gray.800" border="1px solid" borderColor="gray.600"
                                _focus={{ borderColor: 'green.500', boxShadow: 'none' }}
                                py={2} px={3} fontSize="sm" maxH="120px"
                                overflow="auto" flex={1}
                                color="gray.100" _placeholder={{ color: 'gray.500' }}
                                borderRadius="lg"
                            />
                            <IconButton
                                aria-label="Send"
                                onMouseDown={(e) => { e.preventDefault(); handleSend(); }}
                                disabled={isLoading || !input.trim()}
                                colorScheme="green"
                                size="sm"
                                borderRadius="lg"
                                type="button"
                                tabIndex={-1}
                            >
                                <SendIcon />
                            </IconButton>
                        </HStack>
                    </Box>
                </Box>
            </HStack>
        </Box>
    );
};

export default ProjectView;
