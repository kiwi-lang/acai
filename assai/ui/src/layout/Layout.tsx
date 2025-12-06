import { FC, ReactNode, useState, useEffect } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { IconButton, Box, VStack, HStack, Text, Button } from '@chakra-ui/react';
import { assaiAPI } from '../services/api';
import { Conversation } from '../services/types';
import './Layout.css';

// Custom icon components
const HamburgerIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
    <path d="M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z" />
  </svg>
);

const CloseIcon = () => (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
    <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
  </svg>
);

const PlusIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="12" y1="5" x2="12" y2="19" />
    <line x1="5" y1="12" x2="19" y2="12" />
  </svg>
);

const ChatIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </svg>
);

const TrashIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <polyline points="3 6 5 6 21 6" />
    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
  </svg>
);

interface LayoutProps {
  children: ReactNode;
}

const Layout: FC<LayoutProps> = ({ children }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [hoveredConv, setHoveredConv] = useState<string | null>(null);

  useEffect(() => {
    loadConversations();
  }, []);

  const loadConversations = async () => {
    try {
      const convs = await assaiAPI.getConversations();
      setConversations(convs);
    } catch (error) {
      console.error('Failed to load conversations:', error);
    }
  };

  const handleNewChat = () => {
    navigate('/');
    closeMobileMenu();
    // Reload the page to start fresh
    window.location.reload();
  };

  const handleDeleteConversation = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();

    try {
      await assaiAPI.deleteConversation(id);
      setConversations(convs => convs.filter(c => c.id !== id));
    } catch (error) {
      console.error('Failed to delete conversation:', error);
    }
  };

  const toggleMobileMenu = () => {
    setIsMobileMenuOpen(!isMobileMenuOpen);
  };

  const closeMobileMenu = () => {
    setIsMobileMenuOpen(false);
  };

  return (
    <div className="layout" style={{ height: "100vh", width: "100vw", overflow: "hidden" }}>
      {/* Mobile Menu Button */}
      <Box
        position="fixed"
        top={4}
        left={4}
        zIndex={1001}
        display={{ base: 'block', md: 'none' }}
      >
        <IconButton
          aria-label="Toggle menu"
          onClick={toggleMobileMenu}
          colorScheme="green"
          size="lg"
          borderRadius="md"
          boxShadow="lg"
        >
          {isMobileMenuOpen ? <CloseIcon /> : <HamburgerIcon />}
        </IconButton>
      </Box>

      {/* Sidebar */}
      <Box
        className={`sidebar ${isMobileMenuOpen ? 'mobile-open' : ''}`}
        w="260px"
        h="100vh"
        bg="gray.900"
        color="white"
        display="flex"
        flexDirection="column"
        position="fixed"
        left={0}
        top={0}
        zIndex={1000}
        transition="transform 0.3s"
        transform={{ base: isMobileMenuOpen ? 'translateX(0)' : 'translateX(-100%)', md: 'translateX(0)' }}
      >
        {/* Header */}
        <Box p={4} borderBottom="1px solid" borderColor="gray.700">
          <Link to="/" style={{ textDecoration: 'none' }} onClick={closeMobileMenu}>
            <HStack gap={2}>
              <Box
                w="32px"
                h="32px"
                bg="green.500"
                borderRadius="md"
                display="flex"
                alignItems="center"
                justifyContent="center"
                fontWeight="bold"
              >
                AI
              </Box>
              <Text fontSize="xl" fontWeight="bold" color="white">
                ASSAI
              </Text>
            </HStack>
          </Link>
        </Box>

        {/* New Chat Button */}
        <Box p={3}>
          <Button
            w="100%"
            onClick={handleNewChat}
            colorScheme="green"
            variant="outline"
            leftIcon={<PlusIcon />}
            justifyContent="flex-start"
            size="md"
          >
            New Chat
          </Button>
        </Box>

        {/* Conversations List */}
        <VStack
          flex={1}
          gap={1}
          px={2}
          overflowY="auto"
          align="stretch"
          css={{
            '&::-webkit-scrollbar': {
              width: '4px',
            },
            '&::-webkit-scrollbar-track': {
              background: 'transparent',
            },
            '&::-webkit-scrollbar-thumb': {
              background: '#4A5568',
              borderRadius: '2px',
            },
          }}
        >
          {conversations.length === 0 ? (
            <Text fontSize="sm" color="gray.500" textAlign="center" py={4}>
              No conversations yet
            </Text>
          ) : (
            conversations.map((conv) => (
              <Box
                key={conv.id}
                position="relative"
                onMouseEnter={() => setHoveredConv(conv.id)}
                onMouseLeave={() => setHoveredConv(null)}
              >
                <Link
                  to={`/chat/${conv.id}`}
                  style={{ textDecoration: 'none', width: '100%' }}
                  onClick={closeMobileMenu}
                >
                  <HStack
                    p={3}
                    borderRadius="md"
                    bg={location.pathname === `/chat/${conv.id}` ? 'gray.700' : 'transparent'}
                    _hover={{ bg: 'gray.800' }}
                    cursor="pointer"
                    gap={2}
                    justify="space-between"
                  >
                    <HStack gap={2} flex={1} minW={0}>
                      <ChatIcon />
                      <Text
                        fontSize="sm"
                        color="gray.300"
                        noOfLines={1}
                        flex={1}
                      >
                        {conv.title || 'New conversation'}
                      </Text>
                    </HStack>

                    {hoveredConv === conv.id && (
                      <IconButton
                        aria-label="Delete conversation"
                        size="xs"
                        variant="ghost"
                        colorScheme="red"
                        onClick={(e) => handleDeleteConversation(conv.id, e)}
                        opacity={0.7}
                        _hover={{ opacity: 1 }}
                      >
                        <TrashIcon />
                      </IconButton>
                    )}
                  </HStack>
                </Link>
              </Box>
            ))
          )}
        </VStack>

        {/* Footer */}
        <Box p={4} borderTop="1px solid" borderColor="gray.700">
          <VStack gap={1}>
            <Link to="/models" style={{ textDecoration: 'none', width: '100%' }} onClick={closeMobileMenu}>
              <HStack
                p={2}
                borderRadius="md"
                _hover={{ bg: 'gray.800' }}
                cursor="pointer"
                w="100%"
              >
                <Text fontSize="sm" color="gray.400">
                  AI Models
                </Text>
              </HStack>
            </Link>
            <Link to="/api-tester" style={{ textDecoration: 'none', width: '100%' }} onClick={closeMobileMenu}>
              <HStack
                p={2}
                borderRadius="md"
                _hover={{ bg: 'gray.800' }}
                cursor="pointer"
                w="100%"
              >
                <Text fontSize="sm" color="gray.400">
                  API Tester
                </Text>
              </HStack>
            </Link>
          </VStack>
        </Box>
      </Box>

      {/* Main Content */}
      <Box
        className="main-content"
        ml={{ base: 0, md: '260px' }}
        h="100vh"
        w={{ base: '100vw', md: 'calc(100vw - 260px)' }}
        overflow="hidden"
      >
        {children}
      </Box>

      {/* Mobile Overlay */}
      {isMobileMenuOpen && (
        <Box
          position="fixed"
          top={0}
          left={0}
          right={0}
          bottom={0}
          bg="blackAlpha.600"
          zIndex={999}
          onClick={closeMobileMenu}
          display={{ base: 'block', md: 'none' }}
        />
      )}
    </div>
  );
};

export default Layout;
