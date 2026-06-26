import React from 'react';

// Bitcoin (BTC) Icon
export const BtcIcon = ({ size = 24, className = "" }) => (
  <svg 
    className={`inline-block select-none ${className}`} 
    width={size} 
    height={size} 
    viewBox="0 0 24 24" 
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
  >
    <circle cx="12" cy="12" r="11" fill="#F7931A" />
    <path 
      d="M16.14 10.15c.22-.96-.4-1.57-1.42-1.89l.48-1.92-1.17-.3-.47 1.88c-.3-.08-.62-.15-.93-.22l.47-1.89-1.17-.3-.48 1.93c-.25-.06-.5-.11-.75-.17l-1.62-.4-.31 1.25s.87.2.85.22c.48.12.56.44.55.69l-.55 2.2c.03.01.08.02.12.04l-.12-.03-.77 3.09c-.06.15-.2.37-.52.29.01.02-.85-.21-.85-.21l-.58 1.34 1.53.38c.28.07.56.15.84.21l-.49 1.96 1.17.29.48-1.93c.32.09.63.17.93.24l-.48 1.92 1.17.29.49-1.96c2-.38 3.32-1.14 3.05-2.91-.21-1.42-1.28-1.74-2.22-1.92.68-.31 1.2-.84 1.14-1.95zm-3.03 4.88c-.36 1.45-2.8 1.45-3.59 1.45l-.51 2.06c-.4 0-1.15.01-1.15.01l.93-3.71c.78 0 3.33-.25 3.33-.25c.34.09.68.22.86.37a.89.89 0 0 1 .08.07zm.32-3.23c-.33 1.32-2.36 1.32-3.02 1.32l-.46 1.86c-.34 0-.96 0-.96 0l.84-3.37s1.95-.12 2.76-.12c.42.06.66.24.84.31z" 
      fill="white" 
    />
  </svg>
);

// Ethereum (ETH) Icon
export const EthIcon = ({ size = 24, className = "" }) => (
  <svg 
    className={`inline-block select-none ${className}`} 
    width={size} 
    height={size} 
    viewBox="0 0 24 24" 
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
  >
    <circle cx="12" cy="12" r="11" fill="#627EEA" />
    <path d="M12 4.5L11.85 5v10.15l.15.15 4.65-2.75L12 4.5Z" fill="white" fillOpacity="0.602" />
    <path d="M12 4.5L7.35 12.55l4.65 2.75V10.25V4.5Z" fill="white" fillOpacity="0.847" />
    <path d="M12 16.25l-.09.11v4.14l.09.25 4.66-6.5L12 16.25Z" fill="white" fillOpacity="0.602" />
    <path d="M12 20.75v-4.5L7.35 14.25l4.65 6.5Z" fill="white" fillOpacity="0.847" />
    <path d="M12 15.35l4.65-2.75L12 10.25v5.1Z" fill="white" fillOpacity="0.204" />
    <path d="M7.35 12.55l4.65 2.75v-5.1l-4.65-2.65Z" fill="white" fillOpacity="0.602" />
  </svg>
);

// Solana (SOL) Icon
export const SolIcon = ({ size = 24, className = "" }) => (
  <svg 
    className={`inline-block select-none ${className}`} 
    width={size} 
    height={size} 
    viewBox="0 0 24 24" 
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
  >
    <circle cx="12" cy="12" r="11" fill="#14151B" stroke="#252836" strokeWidth="1" />
    <defs>
      <linearGradient id="solana-grad" x1="100%" y1="0%" x2="0%" y2="100%">
        <stop offset="0%" stopColor="#00FFA3" />
        <stop offset="50%" stopColor="#03E1FF" />
        <stop offset="100%" stopColor="#DC1FFF" />
      </linearGradient>
    </defs>
    <g transform="translate(5, 6.5) scale(0.55)">
      <path d="M18.8 3.2L16.2 0.6C16 0.4 15.7 0.3 15.4 0.3H0.8C0.2 0.3 -0.2 0.8 0.1 1.4L2.7 4C2.9 4.2 3.2 4.3 3.5 4.3H18.1C18.7 4.3 19.1 3.8 18.8 3.2Z" fill="url(#solana-grad)"/>
      <path d="M0.1 8.8L2.7 11.4C2.9 11.6 3.2 11.7 3.5 11.7H18.1C18.7 11.7 19.1 11.2 18.8 10.6L16.2 8C16 7.8 15.7 7.7 15.4 7.7H0.8C0.2 7.7 -0.2 8.2 0.1 8.8Z" fill="url(#solana-grad)"/>
      <path d="M18.8 16.2L16.2 13.6C16 13.4 15.7 13.3 15.4 13.3H0.8C0.2 13.3 -0.2 13.8 0.1 14.4L2.7 17C2.9 17.2 3.2 17.3 3.5 17.3H18.1C18.7 17.3 19.1 16.8 18.8 16.2Z" fill="url(#solana-grad)"/>
    </g>
  </svg>
);

// Bybit Exchange Icon (Yellow / Black logo)
export const BybitIcon = ({ size = 24, className = "" }) => (
  <svg 
    className={`inline-block select-none ${className}`} 
    width={size} 
    height={size} 
    viewBox="0 0 24 24" 
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
  >
    <rect width="24" height="24" rx="6" fill="#12131A" stroke="#1d202b" strokeWidth="1" />
    <path 
      d="M7 6H13.25C15.5 6 17 7.3 17 9.25C17 10.5 16.1 11.5 14.8 12C16.3 12.5 17.2 13.6 17.2 15.25C17.2 17.2 15.5 18.5 13.25 18.5H7V6ZM10.5 11H12.5C13.3 11 13.8 10.6 13.8 9.9C13.8 9.2 13.3 8.8 12.5 8.8H10.5V11ZM10.5 16.2H12.7C13.5 16.2 14 15.8 14 15.1C14 14.4 13.5 14 12.7 14H10.5V16.2Z" 
      fill="#FFB11A" 
    />
  </svg>
);

// Bybit Demo Exchange Icon (Alternate Colour - Neon Cyan)
export const BybitDemoIcon = ({ size = 24, className = "" }) => (
  <svg 
    className={`inline-block select-none ${className}`} 
    width={size} 
    height={size} 
    viewBox="0 0 24 24" 
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
  >
    <rect width="24" height="24" rx="6" fill="#12131A" stroke="#1d202b" strokeWidth="1" />
    <path 
      d="M7 6H13.25C15.5 6 17 7.3 17 9.25C17 10.5 16.1 11.5 14.8 12C16.3 12.5 17.2 13.6 17.2 15.25C17.2 17.2 15.5 18.5 13.25 18.5H7V6ZM10.5 11H12.5C13.3 11 13.8 10.6 13.8 9.9C13.8 9.2 13.3 8.8 12.5 8.8H10.5V11ZM10.5 16.2H12.7C13.5 16.2 14 15.8 14 15.1C14 14.4 13.5 14 12.7 14H10.5V16.2Z" 
      fill="#00e5ff" 
    />
  </svg>
);

// Hyperliquid Exchange Icon (Stylized diamond grid in green/cyan)
export const HyperliquidIcon = ({ size = 24, className = "" }) => (
  <svg 
    className={`inline-block select-none ${className}`} 
    width={size} 
    height={size} 
    viewBox="0 0 24 24" 
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
  >
    <rect width="24" height="24" rx="6" fill="#12131A" stroke="#1d202b" strokeWidth="1" />
    <path 
      d="M12 4.5L19.5 12L12 19.5L4.5 12L12 4.5ZM12 7.8L7.8 12L12 16.2L16.2 12L12 7.8Z" 
      fill="#00e5ff" 
      fillOpacity="0.4"
    />
    <path 
      d="M12 9.5L9.5 12L12 14.5L14.5 12L12 9.5Z" 
      fill="#00e5ff" 
    />
  </svg>
);
