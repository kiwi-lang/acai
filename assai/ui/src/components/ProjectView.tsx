import { useState, useEffect, useRef, useCallback, KeyboardEvent } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
    Box, VStack, HStack, Text, Heading, Badge, Textarea, IconButton, Spinner,
    Input, Button, NativeSelect,
} from '@chakra-ui/react';
import { getProject, converse, listTasks, createTask, updateTask, getTaskTree, listProviders, listAgents } from '../services/api';
import { useAgentSocket } from '../contexts/WebSocketContext';
import type { AgentDef, Project, Task, AgentMessage, StreamChunk, Provider } from '../services/types';
import Markdown from './Markdown';

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

const KIND_LABELS: Record<string, { label: string; color: string }> = {
    task: { label: 'Task', color: 'green' },
    llm_complete: { label: 'LLM', color: 'teal' },
    tool_call: { label: 'Tool', color: 'orange' },
};

const KanbanCard = ({ task, onClick }: { task: Task; onClick?: () => void }) => {
    const kind = KIND_LABELS[task.kind] || { label: task.kind, color: 'gray' };
    return (
        <Box
            p={3} bg="var(--bg-input)" borderRadius="md"
            border="1px solid" borderColor="var(--border-secondary)"
            _hover={{ borderColor: 'var(--accent)', bg: 'var(--bg-card-hover)' }}
            transition="all 0.15s"
            cursor="pointer"
            onClick={onClick}
        >
            <HStack justify="space-between" mb={1}>
                <Text fontSize="sm" fontWeight="medium" color="var(--text-heading)" lineClamp={2}>
                    {task.title}
                </Text>
                <Badge
                    colorScheme={kind.color}
                    fontSize="2xs" variant="outline" flexShrink={0}
                >
                    {kind.label}
                </Badge>
            </HStack>
            {task.description && (
                <Text fontSize="xs" color="var(--text-tertiary)" lineClamp={2} mb={1}>
                    {task.description}
                </Text>
            )}
            <HStack gap={2} mt={1}>
                {task.gpu === 1 && (
                    <Badge colorScheme="red" fontSize="2xs" variant="outline">GPU</Badge>
                )}
                <Text fontSize="2xs" color="var(--text-muted)">P{task.priority}</Text>
                {task.retries > 0 && (
                    <Text fontSize="2xs" color="var(--text-secondary)">
                        retry {task.retries}/{task.max_retries}
                    </Text>
                )}
            </HStack>
        </Box>
    );
};

const KanbanColumn = ({ label, tasks, color, onTaskClick }: {
    label: string; tasks: Task[]; color: string; onTaskClick?: (t: Task) => void;
}) => (
    <Box flex={1} minW="180px" maxW="280px">
        <HStack mb={3} gap={2}>
            <Box w="8px" h="8px" borderRadius="full" bg={`${color}.400`} />
            <Text fontSize="sm" fontWeight="semibold" color="var(--text-secondary)">{label}</Text>
            <Badge colorScheme={color} fontSize="2xs" variant="subtle">{tasks.length}</Badge>
        </HStack>
        <VStack
            gap={2} align="stretch"
            bg="var(--bg-kanban-col)" borderRadius="lg" p={2}
            minH="120px" maxH="calc(100vh - 180px)" overflowY="auto"
            border="1px solid" borderColor="var(--border-primary)"
        >
            {tasks.length === 0 ? (
                <Text fontSize="xs" color="var(--text-muted)" textAlign="center" py={4}>Empty</Text>
            ) : (
                tasks.map(t => (
                    <KanbanCard key={t.id} task={t} onClick={() => onTaskClick?.(t)} />
                ))
            )}
        </VStack>
    </Box>
);

const ProjectView = () => {
    const { name } = useParams<{ name: string }>();
    const navigate = useNavigate();
    const { tasks: wsTasks, isConnected, onChunk, onStreamEnd, onStreamError } = useAgentSocket();

    const [project, setProject] = useState<Project | null>(null);
    const [tasks, setTasks] = useState<Task[]>([]);
    const [messages, setMessages] = useState<AgentMessage[]>([]);
    const [input, setInput] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [newTaskTitle, setNewTaskTitle] = useState('');
    const [showNewTask, setShowNewTask] = useState(false);
    const [selectedTask, setSelectedTask] = useState<Task | null>(null);
    const [taskTree, setTaskTree] = useState<Task[]>([]);
    const [providers, setProviders] = useState<Provider[]>([]);
    const [selectedProvider, setSelectedProvider] = useState('auto');
    const [agents, setAgents] = useState<AgentDef[]>([]);
    const [selectedAgent, setSelectedAgent] = useState('default');

    const messagesEndRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const activeTaskRef = useRef<string | null>(null);

    const convIdRef = useRef<string | null>(null);

    useEffect(() => {
        if (!name) return;
        document.title = `${name} - ASSAI`;
        getProject(name).then(setProject).catch(() => navigate('/projects'));
        listProviders().then(setProviders).catch(() => {});
        listAgents().then(setAgents).catch(() => {});
    }, [name, navigate]);

    useEffect(() => {
        if (wsTasks.length > 0) {
            setTasks(wsTasks.filter(t => t.project === name && !t.parent_task));
        } else if (!isConnected && name) {
            listTasks({ project: name, rootOnly: true }).then(setTasks).catch(() => {});
        }
    }, [wsTasks, isConnected, name]);

    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }, [messages]);

    const handleChunk = useCallback((chunk: StreamChunk) => {
        if (chunk.task_id !== activeTaskRef.current) return;
        setMessages(prev => {
            const copy = [...prev];
            const last = copy[copy.length - 1];
            if (last && last.isStreaming && last.taskId === chunk.task_id) {
                copy[copy.length - 1] = { ...last, content: last.content + chunk.token };
            }
            return copy;
        });
    }, []);

    const handleStreamEnd = useCallback((data: { task_id: string }) => {
        if (data.task_id !== activeTaskRef.current) return;
        setMessages(prev => {
            const copy = [...prev];
            const last = copy[copy.length - 1];
            if (last && last.isStreaming && last.taskId === data.task_id) {
                copy[copy.length - 1] = { ...last, isStreaming: false };
            }
            return copy;
        });
        activeTaskRef.current = null;
        setIsLoading(false);
    }, []);

    const handleStreamError = useCallback((data: { task_id: string; error: string }) => {
        if (data.task_id !== activeTaskRef.current) return;
        setMessages(prev => {
            const copy = [...prev];
            const last = copy[copy.length - 1];
            if (last && last.isStreaming && last.taskId === data.task_id) {
                copy[copy.length - 1] = {
                    ...last,
                    content: `⚠ Error: ${data.error}`,
                    isStreaming: false,
                };
            }
            return copy;
        });
        activeTaskRef.current = null;
        setIsLoading(false);
    }, []);

    useEffect(() => {
        const unsub1 = onChunk(handleChunk);
        const unsub2 = onStreamEnd(handleStreamEnd);
        const unsub3 = onStreamError(handleStreamError);
        return () => { unsub1(); unsub2(); unsub3(); };
    }, [onChunk, onStreamEnd, onStreamError, handleChunk, handleStreamEnd, handleStreamError]);

    const handleCreateTask = async () => {
        const title = newTaskTitle.trim();
        if (!title || !name) return;
        try {
            const task = await createTask(title, '', 0, name);
            setTasks(prev => [task, ...prev]);
            setNewTaskTitle('');
            setShowNewTask(false);
        } catch { /* ignore */ }
    };

    const handleTaskClick = async (task: Task) => {
        setSelectedTask(task);
        try {
            const tree = await getTaskTree(task.id);
            setTaskTree(tree);
        } catch {
            setTaskTree([task]);
        }
    };

    const handleSend = async () => {
        const text = input.trim();
        if (!text || isLoading || !name) return;

        setInput('');
        if (textareaRef.current) textareaRef.current.style.height = 'auto';

        setMessages(prev => [...prev, { role: 'user', content: text }]);
        setIsLoading(true);

        try {
            const resp = await converse(text, convIdRef.current || '', name || '', '', selectedProvider, selectedAgent);
            activeTaskRef.current = resp.task_id;
            convIdRef.current = resp.conversation;
            setMessages(prev => [
                ...prev,
                { role: 'assistant', content: '', isStreaming: true, taskId: resp.task_id },
            ]);
        } catch (err) {
            const msg = err instanceof Error ? err.message : 'Request failed';
            setMessages(prev => [...prev, { role: 'assistant', content: `Error: ${msg}` }]);
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
            <Box h="100vh" w="100%" bg="var(--bg-page)" display="flex" alignItems="center" justifyContent="center">
                <Spinner color="var(--accent)" size="lg" />
            </Box>
        );
    }

    return (
        <Box h="100vh" w="100%" bg="var(--bg-page)" display="flex" flexDirection="column" overflow="hidden">
            {/* Header */}
            <HStack
                px={4} py={3}
                borderBottom="1px solid" borderColor="var(--border-primary)"
                bg="var(--bg-page)" flexShrink={0}
            >
                <IconButton
                    aria-label="Back to projects"
                    onClick={() => navigate('/projects')}
                    variant="ghost" size="sm" color="var(--text-tertiary)"
                    _hover={{ color: 'var(--text-heading)' }}
                >
                    <BackIcon />
                </IconButton>
                <HStack gap={2} flex={1}>
                    <Heading size="md" color="var(--text-heading)">{project.name}</Heading>
                    <Badge colorScheme="blue" fontSize="xs" variant="outline">{project.language}</Badge>
                    <Badge colorScheme={project.source === 'clone' ? 'purple' : 'green'} fontSize="xs">
                        {project.source}
                    </Badge>
                </HStack>
                <Text fontSize="xs" color="var(--text-muted)" fontFamily="mono">{project.path}</Text>
            </HStack>

            {/* Main: Kanban + Chat */}
            <HStack flex={1} minH={0} align="stretch" gap={0}>
                {/* Kanban Board */}
                <Box flex={1} overflowX="auto" overflowY="hidden" display="flex" flexDirection="column">
                    <HStack px={4} pt={3} pb={1} gap={2}>
                        {!showNewTask ? (
                            <Button size="xs" colorScheme="green" variant="outline" onClick={() => setShowNewTask(true)}>
                                + New Task
                            </Button>
                        ) : (
                            <>
                                <Input
                                    size="xs" placeholder="Task title" bg="var(--bg-card)"
                                    color="var(--text-heading)" borderColor="var(--border-secondary)" flex={1} maxW="300px"
                                    value={newTaskTitle} onChange={e => setNewTaskTitle(e.target.value)}
                                    onKeyDown={e => e.key === 'Enter' && handleCreateTask()}
                                    autoFocus
                                />
                                <Button size="xs" colorScheme="green" onClick={handleCreateTask} disabled={!newTaskTitle.trim()}>
                                    Add
                                </Button>
                                <Button size="xs" variant="ghost" color="var(--text-tertiary)" onClick={() => { setShowNewTask(false); setNewTaskTitle(''); }}>
                                    Cancel
                                </Button>
                            </>
                        )}
                    </HStack>
                    <Box flex={1} overflowX="auto" overflowY="hidden" p={4} pt={2}>
                        <HStack gap={3} align="flex-start" h="100%" minW="min-content">
                            {COLUMNS.map(col => (
                                <KanbanColumn
                                    key={col.key}
                                    label={col.label}
                                    color={STATUS_COLORS[col.key] || 'gray'}
                                    tasks={tasks.filter(t => t.status === col.key)}
                                    onTaskClick={handleTaskClick}
                                />
                            ))}
                        </HStack>
                    </Box>
                </Box>

                {/* Task Detail Panel (slides in when a task is selected) */}
                {selectedTask && (
                    <Box
                        w="360px" flexShrink={0}
                        borderLeft="1px solid" borderColor="var(--border-primary)"
                        display="flex" flexDirection="column"
                        bg="var(--bg-page)" overflowY="auto"
                    >
                        <HStack px={4} py={3} borderBottom="1px solid" borderColor="var(--border-primary)" justify="space-between">
                            <Text fontSize="sm" fontWeight="semibold" color="var(--text-heading)" lineClamp={1}>
                                {selectedTask.title}
                            </Text>
                            <IconButton
                                aria-label="Close" variant="ghost" size="xs" color="var(--text-tertiary)"
                                _hover={{ color: 'var(--text-heading)' }}
                                onClick={() => { setSelectedTask(null); setTaskTree([]); }}
                            >
                                <Text fontSize="lg">&times;</Text>
                            </IconButton>
                        </HStack>
                        <Box px={4} py={3}>
                            <VStack align="stretch" gap={3}>
                                <HStack gap={2}>
                                    <Badge colorScheme={STATUS_COLORS[selectedTask.status] || 'gray'}>
                                        {selectedTask.status}
                                    </Badge>
                                    <Badge colorScheme={(KIND_LABELS[selectedTask.kind] || { color: 'gray' }).color} variant="outline">
                                        {(KIND_LABELS[selectedTask.kind] || { label: selectedTask.kind }).label}
                                    </Badge>
                                </HStack>
                                {selectedTask.description && (
                                    <Text fontSize="sm" color="var(--text-secondary)">{selectedTask.description}</Text>
                                )}
                                <Text fontSize="xs" color="var(--text-muted)">
                                    Created: {selectedTask.created_at}
                                </Text>
                                {selectedTask.error_log && (
                                    <Box bg="var(--bg-error)" p={2} borderRadius="md">
                                        <Text fontSize="xs" color="var(--text-error)" fontFamily="mono" whiteSpace="pre-wrap">
                                            {selectedTask.error_log}
                                        </Text>
                                    </Box>
                                )}

                                {taskTree.length > 1 && (
                                    <>
                                        <Text fontSize="sm" fontWeight="semibold" color="var(--text-secondary)" mt={2}>
                                            Subtasks ({taskTree.length - 1})
                                        </Text>
                                        <VStack align="stretch" gap={1}>
                                            {taskTree.filter(t => t.id !== selectedTask.id).map(child => (
                                                <HStack
                                                    key={child.id} p={2} bg="var(--bg-card)" borderRadius="md"
                                                    border="1px solid" borderColor="var(--border-primary)" gap={2}
                                                >
                                                    <Badge
                                                        colorScheme={STATUS_COLORS[child.status] || 'gray'}
                                                        fontSize="2xs"
                                                    >
                                                        {child.status}
                                                    </Badge>
                                                    <Text fontSize="xs" color="var(--text-secondary)" flex={1} lineClamp={1}>
                                                        {child.title}
                                                    </Text>
                                                    <Badge
                                                        colorScheme={(KIND_LABELS[child.kind] || { color: 'gray' }).color}
                                                        fontSize="2xs" variant="outline"
                                                    >
                                                        {(KIND_LABELS[child.kind] || { label: child.kind }).label}
                                                    </Badge>
                                                </HStack>
                                            ))}
                                        </VStack>
                                    </>
                                )}
                            </VStack>
                        </Box>
                    </Box>
                )}

                {/* Chat Panel */}
                <Box
                    w="520px" flexShrink={0}
                    borderLeft="1px solid" borderColor="var(--border-primary)"
                    display="flex" flexDirection="column"
                    bg="var(--bg-page)"
                >
                    <Box px={4} py={3} borderBottom="1px solid" borderColor="var(--border-primary)">
                        <Text fontSize="sm" fontWeight="semibold" color="var(--text-secondary)">Chat</Text>
                    </Box>

                    {/* Messages */}
                    <Box flex={1} overflowY="auto" px={3} py={2} minH={0}>
                        <VStack gap={2} align="stretch">
                            {messages.length === 0 && (
                                <Text fontSize="xs" color="var(--text-muted)" textAlign="center" py={6}>
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
                                    bg={msg.role === 'user' ? 'var(--bg-msg-user)' : 'var(--bg-msg-assistant)'}
                                    border="1px solid"
                                    borderColor={msg.role === 'user' ? 'var(--border-msg-user)' : 'var(--border-msg-assistant)'}
                                >
                                    <Text fontSize="xs" color="var(--text-tertiary)" mb={0.5}>
                                        {msg.role === 'user' ? 'You' : 'Agent'}
                                    </Text>
                                    <Markdown content={msg.content} />
                                    {msg.isStreaming && (
                                        <Box as="span" display="inline-block" w="2px" h="0.9em"
                                            bg="var(--cursor-blink)" ml={0.5}
                                            animation="blink 1s step-start infinite" />
                                    )}
                                </Box>
                            ))}
                            {isLoading && !messages.some(m => m.isStreaming) && (
                                <HStack alignSelf="flex-start" gap={2} p={2}>
                                    <Spinner size="xs" color="var(--accent)" />
                                    <Text fontSize="xs" color="var(--text-tertiary)">Thinking...</Text>
                                </HStack>
                            )}
                            <div ref={messagesEndRef} />
                        </VStack>
                    </Box>

                    {/* Input */}
                    <Box px={3} pt={2} pb={2} borderTop="1px solid" borderColor="var(--border-primary)">
                        <HStack mb={1.5} justify="flex-start" gap={3}>
                            <NativeSelect.Root size="xs" w="auto">
                                <NativeSelect.Field
                                    value={selectedAgent}
                                    onChange={e => setSelectedAgent(e.target.value)}
                                    bg="var(--bg-input)"
                                    color="var(--text-tertiary)"
                                    borderColor="var(--border-input)"
                                    fontSize="xs"
                                    px={2}
                                    h="24px"
                                    borderRadius="md"
                                >
                                    {agents.map(a => (
                                        <option key={a.name} value={a.name} style={{ background: 'var(--option-bg)' }}>
                                            {a.avatar ? `${a.avatar} ${a.name}` : a.name}
                                        </option>
                                    ))}
                                </NativeSelect.Field>
                            </NativeSelect.Root>
                            <NativeSelect.Root size="xs" w="auto">
                                <NativeSelect.Field
                                    value={selectedProvider}
                                    onChange={e => setSelectedProvider(e.target.value)}
                                    bg="var(--bg-input)"
                                    color="var(--text-tertiary)"
                                    borderColor="var(--border-input)"
                                    fontSize="xs"
                                    px={2}
                                    h="24px"
                                    borderRadius="md"
                                >
                                    <option value="auto" style={{ background: 'var(--option-bg)' }}>
                                        Auto
                                    </option>
                                    {providers.map(p => (
                                        <option key={p.name} value={p.name} style={{ background: 'var(--option-bg)' }}>
                                            {p.name}
                                        </option>
                                    ))}
                                </NativeSelect.Field>
                            </NativeSelect.Root>
                        </HStack>
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
                                bg="var(--bg-card)" border="1px solid" borderColor="var(--border-secondary)"
                                _focus={{ borderColor: 'var(--accent)', boxShadow: 'none' }}
                                py={2} px={3} fontSize="sm" maxH="120px"
                                overflow="auto" flex={1}
                                color="var(--text-primary)" _placeholder={{ color: 'var(--text-muted)' }}
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
