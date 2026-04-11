import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
    Box, VStack, HStack, Text, Heading, Badge, IconButton, Spinner,
    Input, Button,
} from '@chakra-ui/react';
import { getProject, listTasks, createTask, getTaskTree } from '../services/api';
import { useAgentSocket } from '../contexts/WebSocketContext';
import type { Project, Task } from '../services/types';
import ChatPanel from './ChatPanel';

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
    const { tasks: wsTasks, isConnected } = useAgentSocket();

    const [project, setProject] = useState<Project | null>(null);
    const [tasks, setTasks] = useState<Task[]>([]);
    const [newTaskTitle, setNewTaskTitle] = useState('');
    const [showNewTask, setShowNewTask] = useState(false);
    const [selectedTask, setSelectedTask] = useState<Task | null>(null);
    const [taskTree, setTaskTree] = useState<Task[]>([]);
    const [convId, setConvId] = useState<string | null>(null);

    useEffect(() => {
        if (!name) return;
        document.title = `${name} - ASSAI`;
        getProject(name).then(setProject).catch(() => navigate('/projects'));
    }, [name, navigate]);

    useEffect(() => {
        if (wsTasks.length > 0) {
            setTasks(wsTasks.filter(t => t.project === name && !t.parent_task));
        } else if (!isConnected && name) {
            listTasks({ project: name, rootOnly: true }).then(setTasks).catch(() => {});
        }
    }, [wsTasks, isConnected, name]);

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

    const handleTaskClick = useCallback(async (task: Task) => {
        setSelectedTask(task);
        try {
            const tree = await getTaskTree(task.id);
            setTaskTree(tree);
        } catch {
            setTaskTree([task]);
        }
    }, []);

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

                {/* Task Detail Panel */}
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
                    <ChatPanel
                        conversationId={convId}
                        onConversationCreated={setConvId}
                        project={name}
                        compact
                    />
                </Box>
            </HStack>
        </Box>
    );
};

export default ProjectView;
