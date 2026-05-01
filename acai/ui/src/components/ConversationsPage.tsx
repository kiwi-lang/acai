import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { Box, HStack, Text, IconButton } from '@chakra-ui/react';
import {
    listConversations, updateConversation, deleteConversation,
} from '../services/api';
import type { ConversationMeta } from '../services/types';
import { ConversationSidebar, EditModal } from './ConversationSidebar';
import ChatPanel from './ChatPanel';

const PlusIcon = () => (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
    </svg>
);

const ConversationsPage = () => {
    const { convId } = useParams<{ convId?: string }>();
    const navigate = useNavigate();
    const location = useLocation();

    const [conversations, setConversations] = useState<ConversationMeta[]>([]);
    const [editingConv, setEditingConv] = useState<ConversationMeta | null>(null);

    const [pending] = useState(() => {
        const s = location.state as Record<string, string> | null;
        if (!s?.pendingMessage) return null;
        return {
            message: s.pendingMessage,
            provider: s.provider,
            agent: s.agent,
            thinkingMode: s.thinkingMode as 'off' | 'native' | undefined,
        };
    });

    useEffect(() => {
        if (pending) navigate(location.pathname, { replace: true, state: {} });
    }, []);

    const [readyConvId, setReadyConvId] = useState<string | null>(convId || null);

    useEffect(() => {
        setReadyConvId(convId || null);
    }, [convId]);

    const refreshList = useCallback(() => {
        listConversations().then(setConversations).catch(() => {});
    }, []);

    useEffect(() => {
        document.title = convId ? 'Chat - Açaí' : 'Conversations - Açaí';
        refreshList();
    }, [refreshList, convId]);

    const handleSelect = useCallback((id: string) => {
        navigate(`/conversations/${id}`);
    }, [navigate]);

    const handleEditSave = async (fields: { title?: string; description?: string; tags?: string[] }) => {
        if (!editingConv) return;
        await updateConversation(editingConv.id, fields).catch(() => {});
        setEditingConv(null);
        refreshList();
    };

    const handleDelete = async (id: string) => {
        await deleteConversation(id).catch(() => {});
        setEditingConv(null);
        if (convId === id) navigate('/conversations');
        refreshList();
    };

    const conv = conversations.find(c => c.id === readyConvId);

    const sidebarHeader = (
        <Box px={3} py={3} borderBottom="1px solid" borderColor="var(--border-primary)">
            <HStack justify="space-between">
                <Text fontSize="sm" fontWeight="semibold" color="var(--text-secondary)">
                    Conversations
                </Text>
                <IconButton
                    aria-label="New conversation"
                    onClick={() => navigate('/conversations')}
                    variant="ghost" size="xs" color="var(--text-tertiary)"
                    _hover={{ color: 'var(--text-heading)', bg: 'var(--bg-hover)' }}
                >
                    <PlusIcon />
                </IconButton>
            </HStack>
        </Box>
    );

    return (
        <Box display="flex" h="100vh" w="100%" bg="var(--bg-page)" overflow="hidden">
            <ConversationSidebar
                conversations={conversations}
                activeId={convId || null}
                onSelect={handleSelect}
                onEdit={setEditingConv}
                header={sidebarHeader}
            />

            <ChatPanel
                conversationId={readyConvId}
                onConversationCreated={(id) => {
                    navigate(`/conversations/${id}`, { replace: true });
                    refreshList();
                }}
                onResponseComplete={refreshList}
                initialProvider={pending?.provider || conv?.provider || 'auto'}
                initialAgent={pending?.agent || conv?.agent || (conv?.project ? (conv?.refiner || 'refiner') : 'default')}
                initialThinking={conv?.enable_thinking}
                initialThinkingMode={pending?.thinkingMode}
                autoSendMessage={pending?.message}
                onProviderChange={(v) => {
                    if (convId) updateConversation(convId, { provider: v }).catch(() => {});
                }}
                onAgentChange={(v) => {
                    if (convId) updateConversation(convId, { agent: v }).catch(() => {});
                }}
            />

            {editingConv && (
                <EditModal
                    conv={editingConv}
                    onSave={handleEditSave}
                    onDelete={handleDelete}
                    onClose={() => setEditingConv(null)}
                />
            )}
        </Box>
    );
};

export default ConversationsPage;
