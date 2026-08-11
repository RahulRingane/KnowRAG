"use client";

import * as React from "react";

import { ErrorState } from "@/components/layout/error-state";

interface ErrorBoundaryProps {
  children: React.ReactNode;
  /** Custom fallback; defaults to `ErrorState`. Receives the caught error and a reset callback. */
  fallback?: (error: Error, reset: () => void) => React.ReactNode;
  /** Called when an error is caught, e.g. to report to an error tracker. */
  onError?: (error: Error, info: React.ErrorInfo) => void;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Generic React error boundary for client components that can throw
 * synchronously during render (not for async fetch errors — those are
 * TanStack Query's job via its own `error` state; see §5.3's four-state
 * rule). Use around a component tree you want to isolate from a full-page
 * crash, e.g. a single card in a dashboard of many.
 */
export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    this.props.onError?.(error, info);
  }

  reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (error) {
      if (this.props.fallback) return this.props.fallback(error, this.reset);
      return <ErrorState message={error.message} onRetry={this.reset} />;
    }
    return this.props.children;
  }
}
