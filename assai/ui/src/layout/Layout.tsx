import { FC, ReactNode, useState, useEffect } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { IconButton, Box, VStack, HStack, Text, Button } from '@chakra-ui/react';
import { assaiAPI } from '../services/api';
import { Conversation } from '../services/types';
import TelemetryDisplay from '../components/TelemetryDisplay';
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

// Task type icons
const Text2TextIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </svg>
);

const Text2ImageIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
    <circle cx="8.5" cy="8.5" r="1.5" />
    <polyline points="21 15 16 10 5 21" />
  </svg>
);

const Text2SpeechIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
    <line x1="12" y1="19" x2="12" y2="23" />
    <line x1="8" y1="23" x2="16" y2="23" />
  </svg>
);

const Text2AudioIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
    <path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07" />
  </svg>
);

const Image2TextIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
    <circle cx="8.5" cy="8.5" r="1.5" />
    <polyline points="21 15 16 10 5 21" />
    <path d="M12 8v8M8 12h8" />
  </svg>
);

const Speech2TextIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
    <path d="M12 19v4M8 23h8" />
    <path d="M12 15v4" />
  </svg>
);

// Task type definitions
const taskTypes = [
  { id: 'text2text', name: 'Text to Text', path: '/', icon: Text2TextIcon, color: 'blue' },
  { id: 'text2image', name: 'Text to Image', path: '/text2image', icon: Text2ImageIcon, color: 'purple' },
  { id: 'text2speech', name: 'Text to Speech', path: '/text2speech', icon: Text2SpeechIcon, color: 'green' },
  { id: 'text2audio', name: 'Text to Audio', path: '/text2audio', icon: Text2AudioIcon, color: 'teal' },
  { id: 'image2text', name: 'Image to Text', path: '/image2text', icon: Image2TextIcon, color: 'orange' },
  { id: 'speech2text', name: 'Speech to Text', path: '/speech2text', icon: Speech2TextIcon, color: 'pink' },
];

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

        {/* Task Selector */}
        <Box px={3} pb={3}>
          <Text fontSize="xs" fontWeight="bold" color="gray.500" mb={2} px={2} textTransform="uppercase" letterSpacing="wide">
            Tasks
          </Text>
          <VStack gap={1} align="stretch">
            {taskTypes.map((task) => {
              const Icon = task.icon;
              const isActive = location.pathname === task.path ||
                (task.path === '/' && location.pathname.startsWith('/chat/'));
              return (
                <Link
                  key={task.id}
                  to={task.path}
                  style={{ textDecoration: 'none', width: '100%' }}
                  onClick={closeMobileMenu}
                >
                  <HStack
                    p={2.5}
                    borderRadius="md"
                    bg={isActive ? 'gray.700' : 'transparent'}
                    _hover={{ bg: 'gray.800' }}
                    cursor="pointer"
                    gap={3}
                    transition="all 0.2s"
                  >
                    <Box color={isActive ? `${task.color}.300` : 'gray.400'}>
                      <Icon />
                    </Box>
                    <Text
                      fontSize="sm"
                      color={isActive ? 'white' : 'gray.300'}
                      fontWeight={isActive ? 'medium' : 'normal'}
                    >
                      {task.name}
                    </Text>
                  </HStack>
                </Link>
              );
            })}
          </VStack>
        </Box>

        {/* Conversations List */}
        <Box flex={1} overflowY="auto" px={2}>
          <Text fontSize="xs" fontWeight="bold" color="gray.500" mb={2} px={2} textTransform="uppercase" letterSpacing="wide">
            Recent Chats
          </Text>
          <VStack
            gap={1}
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
        </Box>

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

          {/* Telemetry Display */}
          <TelemetryDisplay />
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
