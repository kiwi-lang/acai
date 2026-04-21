import { useState, useEffect } from 'react';
import {
    Box, VStack, HStack, Text, Button, Heading, Badge,
    Input, Textarea, Spinner,
} from '@chakra-ui/react';
import { listTasks, createTask, getTaskTree, listProjects } from '../services/api';
import { useAgentSocket } from '../contexts/WebSocketContext';
import type { Task, Project } from '../services/types';

const STATUS_COLORS: Record<string, string> = {
    pending: 'yellow',
    curating: 'orange',
    ready: 'blue',
    in_progress: 'cyan',
    completed: 'green',
    failed: 'red',
    review: 'purple',
};

const KIND_LABELS: Record<string, { label: string; color: string }> = {
    task: { label: 'Task', color: 'green' },
    llm_complete: { label: 'LLM', color: 'teal' },
    tool_call: { label: 'Tool', color: 'orange' },
};

const DetailRow = ({ label, value }: { label: string; value: string | number | null | undefined }) => {
    const display = value === null || value === undefined || value === '' ? '—' : String(value);
    const isEmpty = display === '—';
    return (
        <HStack gap={2} py={1} borderBottom="1px solid" borderColor="var(--border-primary)" align="flex-start">
            <Text fontSize="xs" color="var(--text-muted)" fontWeight="medium" w="120px" flexShrink={0}>
                {label}
            </Text>
            <Text
                fontSize="xs"
                color={isEmpty ? 'var(--text-muted)' : 'var(--text-secondary)'}
                fontFamily="mono"
                wordBreak="break-all"
                flex={1}
                whiteSpace="pre-wrap"
            >
                {display}
            </Text>
        </HStack>
    );
};

const TaskDetail = ({ task }: { task: Task }) => (
    <Box
        mt={1} p={4} bg="var(--bg-elevated)" borderRadius="md"
        border="1px solid" borderColor="var(--border-secondary)"
    >
        <Text fontSize="sm" fontWeight="semibold" color="var(--text-heading)" mb={3}>
            DB Record
        </Text>
        <VStack align="stretch" gap={0}>
            <DetailRow label="id" value={task.id} />
            <DetailRow label="kind" value={task.kind} />
            <DetailRow label="status" value={task.status} />
            <DetailRow label="title" value={task.title} />
            <DetailRow label="description" value={task.description} />
            <DetailRow label="priority" value={task.priority} />
            <DetailRow label="gpu" value={task.gpu} />
            <DetailRow label="agent" value={task.agent} />
            <DetailRow label="project" value={task.project} />
            <DetailRow label="spec" value={task.spec} />
            <DetailRow label="spec_path" value={task.spec_path} />
            <DetailRow label="context_path" value={task.context_path} />
            <DetailRow label="result_path" value={task.result_path} />
            <DetailRow label="worktree" value={task.worktree} />
            <DetailRow label="assigned_to" value={task.assigned_to} />
            <DetailRow label="depends_on" value={task.depends_on} />
            <DetailRow label="parent_task" value={task.parent_task} />
            <DetailRow label="root_task" value={task.root_task} />
            <DetailRow label="retries" value={`${task.retries} / ${task.max_retries}`} />
            <DetailRow label="error_log" value={task.error_log} />
            <DetailRow label="created_at" value={task.created_at} />
            <DetailRow label="updated_at" value={task.updated_at} />
            <DetailRow label="started_at" value={task.started_at} />
        </VStack>
    </Box>
);

const ChildRow = ({ task, depth = 0, onSelect, selectedId }: {
    task: Task; depth?: number;
    onSelect?: (task: Task) => void;
    selectedId?: string | null;
}) => {
    const kind = KIND_LABELS[task.kind] || { label: task.kind, color: 'gray' };
    const isSelected = selectedId === task.id;
    return (
        <Box>
            <HStack
                pl={`${depth * 20 + 8}px`} py={1.5} pr={3}
                bg={isSelected ? 'var(--bg-active)' : 'var(--bg-elevated)'} borderRadius="md"
                border="1px solid" borderColor={isSelected ? 'var(--accent)' : 'var(--border-primary)'} gap={2}
                cursor="pointer"
                _hover={{ borderColor: 'var(--border-secondary)' }}
                onClick={(e) => { e.stopPropagation(); onSelect?.(task); }}
            >
                <Badge colorScheme={STATUS_COLORS[task.status] || 'gray'} fontSize="2xs">{task.status}</Badge>
                <Badge colorScheme={kind.color} fontSize="2xs" variant="outline">{kind.label}</Badge>
                <Text fontSize="xs" color="var(--text-secondary)" flex={1} lineClamp={1}>{task.title}</Text>
                {task.created_at && (
                    <Text fontSize="2xs" color="var(--text-muted)">{new Date(task.created_at).toLocaleString()}</Text>
                )}
            </HStack>
            {isSelected && <TaskDetail task={task} />}
        </Box>
    );
};

const TaskCard = ({ task, rootOnly }: { task: Task; rootOnly: boolean }) => {
    const [expanded, setExpanded] = useState(false);
    const [showDetail, setShowDetail] = useState(false);
    const [children, setChildren] = useState<Task[]>([]);
    const [loadingTree, setLoadingTree] = useState(false);
    const [selectedChild, setSelectedChild] = useState<string | null>(null);

    const handleExpand = async () => {
        if (expanded) { setExpanded(false); return; }
        setLoadingTree(true);
        try {
            const tree = await getTaskTree(task.id);
            setChildren(tree.filter(t => t.id !== task.id));
        } catch { setChildren([]); }
        setLoadingTree(false);
        setExpanded(true);
    };

    const handleChildSelect = (child: Task) => {
        setSelectedChild(prev => prev === child.id ? null : child.id);
    };

    const kind = KIND_LABELS[task.kind] || { label: task.kind, color: 'gray' };

    return (
        <Box>
            <Box
                p={4} bg="var(--bg-card)" borderRadius="lg"
                border="1px solid" borderColor={expanded || showDetail ? 'var(--accent)' : 'var(--border-primary)'}
                _hover={{ borderColor: 'var(--border-secondary)' }}
                transition="all 0.2s"
                cursor="pointer"
                onClick={rootOnly ? handleExpand : () => setShowDetail(v => !v)}
            >
                <HStack justify="space-between" mb={2}>
                    <HStack gap={2}>
                        {rootOnly && (
                            <Text fontSize="xs" color="var(--text-muted)" mr={1}>
                                {expanded ? '▾' : '▸'}
                            </Text>
                        )}
                        <Badge colorScheme={kind.color} fontSize="xs" variant="outline">
                            {kind.label}
                        </Badge>
                        {task.gpu === 1 && (
                            <Badge colorScheme="red" fontSize="xs" variant="outline">GPU</Badge>
                        )}
                        <Text fontWeight="semibold" color="var(--text-heading)" fontSize="md">
                            {task.title}
                        </Text>
                    </HStack>
                    <HStack gap={2}>
                        {task.project && (
                            <Badge colorScheme="purple" fontSize="xs" variant="outline">{task.project}</Badge>
                        )}
                        <Badge colorScheme={STATUS_COLORS[task.status] || 'gray'} fontSize="xs">
                            {task.status}
                        </Badge>
                    </HStack>
                </HStack>

                {task.description && (
                    <Text fontSize="sm" color="var(--text-tertiary)" mb={3} lineHeight="1.6">
                        {task.description}
                    </Text>
                )}

                <HStack gap={4} flexWrap="wrap">
                    <Text fontSize="xs" color="var(--text-muted)">Priority: {task.priority}</Text>
                    {task.assigned_to && (
                        <Text fontSize="xs" color="var(--text-muted)">Assigned: {task.assigned_to}</Text>
                    )}
                    {task.retries > 0 && (
                        <Text fontSize="xs" color="orange.400">Retries: {task.retries}/{task.max_retries}</Text>
                    )}
                    {task.created_at && (
                        <Text fontSize="xs" color="var(--text-muted)">{new Date(task.created_at).toLocaleString()}</Text>
                    )}
                    {rootOnly && (
                        <Text
                            fontSize="xs" color="var(--text-link)"
                            cursor="pointer" _hover={{ textDecoration: 'underline' }}
                            onClick={(e) => { e.stopPropagation(); setShowDetail(v => !v); }}
                        >
                            {showDetail ? 'Hide details' : 'Details'}
                        </Text>
                    )}
                </HStack>

                {task.error_log && (
                    <Box mt={2} p={2} bg="var(--bg-error)" borderRadius="md" maxH="100px" overflowY="auto">
                        <Text fontSize="xs" color="var(--text-error)" fontFamily="mono" whiteSpace="pre-wrap">
                            {task.error_log}
                        </Text>
                    </Box>
                )}
            </Box>

            {showDetail && <TaskDetail task={task} />}

            {expanded && (
                <VStack align="stretch" gap={1} ml={4} mt={1} mb={2}>
                    {loadingTree ? (
                        <HStack gap={2} p={2}><Spinner size="xs" color="green.300" /><Text fontSize="xs" color="var(--text-tertiary)">Loading tree...</Text></HStack>
                    ) : children.length === 0 ? (
                        <Text fontSize="xs" color="var(--text-muted)" pl={3} py={1}>No subtasks</Text>
                    ) : (
                        children.map(child => (
                            <ChildRow
                                key={child.id} task={child}
                                onSelect={handleChildSelect}
                                selectedId={selectedChild}
                            />
                        ))
                    )}
                </VStack>
            )}
        </Box>
    );
};

const TasksPage = () => {
    const { tasks: wsTasks, isConnected } = useAgentSocket();
    const [tasks, setTasks] = useState<Task[]>([]);
    const [filter, setFilter] = useState<string | null>(null);
    const [projectFilter, setProjectFilter] = useState<string>('');
    const [projects, setProjects] = useState<Project[]>([]);
    const [rootOnly, setRootOnly] = useState(true);
    const [showCreate, setShowCreate] = useState(false);
    const [newTitle, setNewTitle] = useState('');
    const [newDesc, setNewDesc] = useState('');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        listProjects().then(setProjects).catch(() => {});
    }, []);

    useEffect(() => {
        if (wsTasks.length > 0) {
            let filtered = wsTasks;
            if (filter) filtered = filtered.filter(t => t.status === filter);
            if (projectFilter) filtered = filtered.filter(t => t.project === projectFilter);
            if (rootOnly) filtered = filtered.filter(t => !t.parent_task);
            setTasks(filtered);
            setLoading(false);
        }
    }, [wsTasks, filter, projectFilter, rootOnly]);

    useEffect(() => {
        document.title = 'Work Queue - Açaí';
        if (!isConnected) {
            listTasks({
                status: filter ?? undefined,
                project: projectFilter || undefined,
                rootOnly,
            })
                .then(data => { setTasks(data); setLoading(false); })
                .catch(() => setLoading(false));
        }
    }, [isConnected, filter, projectFilter, rootOnly]);

    const handleCreate = async () => {
        if (!newTitle.trim()) return;
        try {
            await createTask(newTitle.trim(), newDesc.trim(), 0, projectFilter);
            setNewTitle('');
            setNewDesc('');
            setShowCreate(false);
        } catch { /* ignore */ }
    };

    const statuses = ['pending', 'curating', 'ready', 'in_progress', 'completed', 'failed', 'review'];

    return (
        <Box h="100vh" w="100%" bg="var(--bg-page)" overflowY="auto" p={6}>
            <Box maxW="4xl" mx="auto">
                <HStack justify="space-between" mb={6}>
                    <Heading size="lg" color="var(--text-heading)">Work Queue</Heading>
                    <HStack gap={2}>
                        <Button
                            colorScheme="green" size="sm"
                            onClick={() => setShowCreate(!showCreate)}
                        >
                            {showCreate ? 'Cancel' : 'New Task'}
                        </Button>
                    </HStack>
                </HStack>

                {showCreate && (
                    <Box p={4} bg="var(--bg-card)" borderRadius="lg" mb={6} border="1px solid" borderColor="var(--border-primary)">
                        <VStack gap={3} align="stretch">
                            <Input
                                placeholder="Task title"
                                value={newTitle}
                                onChange={e => setNewTitle(e.target.value)}
                                bg="var(--bg-input)" border="none" color="var(--text-heading)"
                                _placeholder={{ color: 'var(--text-muted)' }}
                            />
                            <Textarea
                                placeholder="Description (optional)"
                                value={newDesc}
                                onChange={e => setNewDesc(e.target.value)}
                                bg="var(--bg-input)" border="none" color="var(--text-heading)"
                                _placeholder={{ color: 'var(--text-muted)' }}
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

                {/* Filters row */}
                <HStack gap={3} mb={4} flexWrap="wrap" align="center">
                    <HStack gap={1} bg="var(--bg-card)" borderRadius="md" p={1} border="1px solid" borderColor="var(--border-primary)">
                        <Button
                            size="xs" variant="ghost"
                            onClick={() => setProjectFilter('')}
                            bg={!projectFilter ? 'var(--bg-active)' : 'transparent'}
                            color={!projectFilter ? 'var(--text-heading)' : 'var(--text-tertiary)'}
                            _hover={{ bg: 'var(--bg-hover)' }}
                        >
                            All Projects
                        </Button>
                        {projects.map(p => (
                            <Button
                                key={p.name} size="xs"
                                variant="ghost"
                                onClick={() => setProjectFilter(projectFilter === p.name ? '' : p.name)}
                                bg={projectFilter === p.name ? 'var(--accent-subtle)' : 'transparent'}
                                color={projectFilter === p.name ? 'var(--text-heading)' : 'var(--text-tertiary)'}
                                _hover={{ bg: 'var(--bg-hover)' }}
                            >
                                {p.name}
                            </Button>
                        ))}
                    </HStack>

                    <Button
                        size="xs"
                        variant={rootOnly ? 'solid' : 'outline'}
                        colorScheme="green"
                        onClick={() => setRootOnly(!rootOnly)}
                    >
                        {rootOnly ? 'Root tasks only' : 'All tasks'}
                    </Button>
                </HStack>

                {/* Status filters */}
                <HStack gap={2} mb={6} flexWrap="wrap">
                    <Button
                        size="xs" variant="ghost"
                        onClick={() => setFilter(null)}
                        bg={filter === null ? 'var(--bg-active)' : 'transparent'}
                        color={filter === null ? 'var(--text-heading)' : 'var(--text-tertiary)'}
                        border="1px solid"
                        borderColor={filter === null ? 'var(--border-secondary)' : 'var(--border-primary)'}
                        _hover={{ bg: 'var(--bg-hover)' }}
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
                        <Text color="var(--text-tertiary)" textAlign="center" py={8}>Loading...</Text>
                    ) : tasks.length === 0 ? (
                        <VStack py={12} gap={3}>
                            <Text fontSize="lg" color="var(--text-tertiary)">No tasks in the queue</Text>
                            <Text fontSize="sm" color="var(--text-muted)">
                                Tasks are created during conversation or manually above.
                            </Text>
                        </VStack>
                    ) : (
                        tasks.map(task => <TaskCard key={task.id} task={task} rootOnly={rootOnly} />)
                    )}
                </VStack>
            </Box>
        </Box>
    );
};

export default TasksPage;
