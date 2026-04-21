import { useState, useEffect, useCallback } from 'react';
import { Box, HStack, Text } from '@chakra-ui/react';
import { listConversations, updateConversation, deleteConversation } from '../services/api';
import type { ConversationMeta } from '../services/types';
import { ConversationSidebar, EditModal } from './ConversationSidebar';
import ChatPanel from './ChatPanel';

const UberChat = () => {
    const [conversations, setConversations] = useState<ConversationMeta[]>([]);
    const [activeConv, setActiveConv] = useState<string | null>(null);
    const [editingConv, setEditingConv] = useState<ConversationMeta | null>(null);

    const refreshConversations = useCallback(() => {
        listConversations().then(setConversations).catch(() => {});
    }, []);

    useEffect(() => {
        document.title = 'Uber Chat - Açaí';
        refreshConversations();
    }, [refreshConversations]);

    const handleRoute = useCallback((_data: { conversation: string; is_new: boolean; title: string }) => {
        refreshConversations();
    }, [refreshConversations]);

    const handleEditSave = async (fields: { title?: string; description?: string; tags?: string[] }) => {
        if (!editingConv) return;
        await updateConversation(editingConv.id, fields).catch(() => {});
        setEditingConv(null);
        refreshConversations();
    };

    const handleDelete = async (convId: string) => {
        await deleteConversation(convId).catch(() => {});
        setEditingConv(null);
        if (activeConv === convId) setActiveConv(null);
        refreshConversations();
    };

    const uberHeader = (
        <Box px={3} py={3} borderBottom="1px solid" borderColor="var(--border-primary)">
            <HStack>
                <Box w="24px" h="24px" borderRadius="sm"
                    display="flex" alignItems="center" justifyContent="center"
                    bg="linear-gradient(135deg, #667eea 0%, #764ba2 100%)"
                    color="white" fontWeight="bold" fontSize="2xs" flexShrink={0}>
                    U
                </Box>
                <Text fontSize="sm" fontWeight="semibold" color="var(--text-secondary)">
                    Uber Chat
                </Text>
            </HStack>
        </Box>
    );

    return (
        <Box display="flex" h="100vh" w="100%" bg="var(--bg-page)" overflow="hidden">
            <ConversationSidebar
                conversations={conversations}
                activeId={activeConv}
                onSelect={setActiveConv}
                onEdit={setEditingConv}
                header={uberHeader}
            />

            <ChatPanel
                conversationId={activeConv}
                onConversationCreated={setActiveConv}
                mode="uber"
                onRoute={handleRoute}
                onResponseComplete={refreshConversations}
                placeholder="Ask anything — conversations are picked automatically..."
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

export default UberChat;
