import { useState, type ReactNode } from 'react';
import {
    Box, VStack, HStack, Text, IconButton,
    Badge, Input, Textarea,
} from '@chakra-ui/react';
import type { ConversationMeta } from '../services/types';

/* ─── Icons ──────────────────────────────────────────────────────── */

const EditIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
);

const CloseIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
    </svg>
);

const CheckIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="20 6 9 17 4 12" />
    </svg>
);

const TrashIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="3 6 5 6 21 6" />
        <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
        <path d="M10 11v6" /><path d="M14 11v6" />
        <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </svg>
);

/* ─── Edit modal ─────────────────────────────────────────────────── */

export interface EditModalProps {
    conv: ConversationMeta;
    onSave: (fields: { title?: string; description?: string; tags?: string[] }) => void;
    onDelete: (id: string) => void;
    onClose: () => void;
}

export const EditModal = ({ conv, onSave, onDelete, onClose }: EditModalProps) => {
    const [title, setTitle] = useState(conv.title);
    const [description, setDescription] = useState(conv.description || '');
    const [tagsStr, setTagsStr] = useState((conv.tags || []).join(', '));
    const [confirmDelete, setConfirmDelete] = useState(false);

    const handleSave = () => {
        const tags = tagsStr.split(',').map(t => t.trim()).filter(Boolean);
        onSave({ title, description, tags });
    };

    return (
        <Box position="fixed" inset={0} bg="rgba(0,0,0,0.5)" zIndex={1000}
            display="flex" alignItems="center" justifyContent="center"
            onClick={onClose}>
            <Box bg="var(--bg-card)" p={6} borderRadius="lg" minW="420px" maxW="520px"
                border="1px solid" borderColor="var(--border-primary)"
                boxShadow="xl" onClick={e => e.stopPropagation()}>
                <HStack justify="space-between" mb={4}>
                    <Text fontWeight="semibold" color="var(--text-heading)">Edit Conversation</Text>
                    <IconButton aria-label="Close" variant="ghost" size="xs" onClick={onClose}>
                        <CloseIcon />
                    </IconButton>
                </HStack>

                <VStack gap={3} align="stretch">
                    <Box>
                        <Text fontSize="xs" color="var(--text-muted)" mb={1}>Title</Text>
                        <Input value={title} onChange={e => setTitle(e.target.value)}
                            bg="var(--bg-input)" borderColor="var(--border-input)"
                            color="var(--text-primary)" fontSize="sm" />
                    </Box>
                    <Box>
                        <Text fontSize="xs" color="var(--text-muted)" mb={1}>Description</Text>
                        <Textarea value={description} onChange={e => setDescription(e.target.value)}
                            bg="var(--bg-input)" borderColor="var(--border-input)"
                            color="var(--text-primary)" fontSize="sm" rows={3} resize="vertical" />
                    </Box>
                    <Box>
                        <Text fontSize="xs" color="var(--text-muted)" mb={1}>Tags (comma-separated)</Text>
                        <Input value={tagsStr} onChange={e => setTagsStr(e.target.value)}
                            bg="var(--bg-input)" borderColor="var(--border-input)"
                            color="var(--text-primary)" fontSize="sm"
                            placeholder="python, api, backend" />
                    </Box>
                </VStack>

                <HStack justify="space-between" mt={5}>
                    {confirmDelete ? (
                        <HStack gap={2}>
                            <Text fontSize="xs" color="red.400">Delete?</Text>
                            <IconButton aria-label="Confirm delete" size="sm" variant="ghost"
                                color="red.400" _hover={{ bg: 'rgba(229,62,62,0.15)' }}
                                onClick={() => onDelete(conv.id)}>
                                <CheckIcon />
                            </IconButton>
                            <IconButton aria-label="Cancel delete" size="sm" variant="ghost"
                                color="var(--text-muted)" _hover={{ color: 'var(--text-heading)' }}
                                onClick={() => setConfirmDelete(false)}>
                                <CloseIcon />
                            </IconButton>
                        </HStack>
                    ) : (
                        <IconButton aria-label="Delete conversation" variant="ghost" size="sm"
                            color="var(--text-muted)" _hover={{ color: 'red.400' }}
                            onClick={() => setConfirmDelete(true)}>
                            <TrashIcon />
                        </IconButton>
                    )}
                    <HStack gap={2}>
                        <IconButton aria-label="Cancel" variant="ghost" size="sm" onClick={onClose}
                            color="var(--text-muted)" _hover={{ color: 'var(--text-heading)' }}>
                            <CloseIcon />
                        </IconButton>
                        <IconButton aria-label="Save" size="sm" onClick={handleSave}
                            colorScheme="green">
                            <CheckIcon />
                        </IconButton>
                    </HStack>
                </HStack>
            </Box>
        </Box>
    );
};

/* ─── Sidebar ────────────────────────────────────────────────────── */

export interface ConversationSidebarProps {
    conversations: ConversationMeta[];
    activeId: string | null;
    onSelect: (id: string) => void;
    onEdit: (conv: ConversationMeta) => void;
    header?: ReactNode;
}

export const ConversationSidebar = ({ conversations, activeId, onSelect, onEdit, header }: ConversationSidebarProps) => (
    <Box
        w="260px" flexShrink={0}
        borderRight="1px solid" borderColor="var(--border-primary)"
        display="flex" flexDirection="column"
        bg="var(--bg-page)"
    >
        {header || (
            <Box px={3} py={3} borderBottom="1px solid" borderColor="var(--border-primary)">
                <Text fontSize="sm" fontWeight="semibold" color="var(--text-secondary)">
                    Conversations
                </Text>
            </Box>
        )}
        <Box flex={1} overflowY="auto" px={2} py={2}>
            <VStack gap={1} align="stretch">
                {conversations.length === 0 && (
                    <Text fontSize="xs" color="var(--text-muted)" textAlign="center" py={4}>
                        No conversations yet
                    </Text>
                )}
                {conversations.map(c => (
                    <HStack
                        key={c.id} px={3} py={2} borderRadius="md" cursor="pointer"
                        bg={activeId === c.id ? 'var(--bg-active)' : 'transparent'}
                        _hover={{ bg: activeId === c.id ? 'var(--bg-active)' : 'var(--bg-hover)' }}
                        onClick={() => onSelect(c.id)}
                        role="group"
                    >
                        <VStack align="flex-start" flex={1} gap={0}>
                            <Text fontSize="sm" lineClamp={1}
                                color={activeId === c.id ? 'var(--text-heading)' : 'var(--text-tertiary)'}>
                                {c.title || 'Untitled'}
                            </Text>
                            {c.tags && c.tags.length > 0 && (
                                <HStack gap={1} flexWrap="wrap">
                                    {c.tags.slice(0, 3).map(tag => (
                                        <Badge key={tag} fontSize="2xs" px={1.5} py={0}
                                            borderRadius="full" bg="rgba(102,126,234,0.12)"
                                            color="var(--text-muted)" fontWeight="normal">
                                            {tag}
                                        </Badge>
                                    ))}
                                </HStack>
                            )}
                        </VStack>
                        <IconButton
                            aria-label="Edit"
                            onClick={(e) => { e.stopPropagation(); onEdit(c); }}
                            variant="ghost" size="xs"
                            color="var(--text-muted)"
                            _hover={{ color: 'var(--text-heading)', bg: 'transparent' }}
                        >
                            <EditIcon />
                        </IconButton>
                    </HStack>
                ))}
            </VStack>
        </Box>
    </Box>
);
