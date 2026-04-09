import { FC, ReactNode, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { IconButton, Box, VStack, HStack, Text, Button } from '@chakra-ui/react';
import { ColorModeButton } from '../components/ui/color-mode';
import './Layout.css';

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
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </svg>
);

const TasksIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M9 11l3 3L22 4" />
    <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
  </svg>
);

const ProjectsIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
  </svg>
);

const StatusIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
  </svg>
);

const navItems = [
  { id: 'chat', name: 'Conversation', path: '/', icon: ChatIcon, color: 'green' },
  { id: 'projects', name: 'Projects', path: '/projects', icon: ProjectsIcon, color: 'orange' },
  { id: 'tasks', name: 'Work Queue', path: '/tasks', icon: TasksIcon, color: 'blue' },
  { id: 'status', name: 'Status', path: '/status', icon: StatusIcon, color: 'purple' },
];

interface LayoutProps {
  children: ReactNode;
}

const Layout: FC<LayoutProps> = ({ children }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  const handleNewChat = () => {
    navigate('/');
    setIsMobileMenuOpen(false);
    window.location.reload();
  };

  const closeMobileMenu = () => setIsMobileMenuOpen(false);

  return (
    <div className="layout" style={{ height: '100vh', width: '100vw', overflow: 'hidden' }}>
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
          onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
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
        borderRight="1px solid"
        borderColor="gray.700"
      >
        {/* Header */}
        <Box p={4} borderBottom="1px solid" borderColor="gray.700">
          <Link to="/" style={{ textDecoration: 'none' }} onClick={closeMobileMenu}>
            <HStack gap={2}>
              <ColorModeButton />
              <Text fontSize="xl" fontWeight="bold" color="white">
                AS
              </Text>
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
                SAI
              </Box>
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
            justifyContent="flex-start"
            size="md"
            color="gray.200"
            borderColor="gray.600"
            _hover={{ bg: 'gray.700', borderColor: 'gray.500' }}
          >
            <HStack gap={2}>
              <PlusIcon />
              <Text>New Chat</Text>
            </HStack>
          </Button>
        </Box>

        {/* Navigation */}
        <Box px={3} pb={3} flex={1}>
          <Text fontSize="xs" fontWeight="bold" color="gray.500" mb={2} px={2} textTransform="uppercase" letterSpacing="wide">
            Navigation
          </Text>
          <VStack gap={1} align="stretch">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = location.pathname === item.path;
              return (
                <Link
                  key={item.id}
                  to={item.path}
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
                    <Box color={isActive ? `${item.color}.300` : 'gray.400'}>
                      <Icon />
                    </Box>
                    <Text
                      fontSize="sm"
                      color={isActive ? 'white' : 'gray.300'}
                      fontWeight={isActive ? 'medium' : 'normal'}
                    >
                      {item.name}
                    </Text>
                  </HStack>
                </Link>
              );
            })}
          </VStack>
        </Box>

        {/* Footer */}
        <Box p={4} borderTop="1px solid" borderColor="gray.700">
          <Text fontSize="xs" color="gray.500" textAlign="center">
            assai v0.1.0
          </Text>
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
