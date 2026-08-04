import React, { useState, useEffect } from 'react';
import api from '../lib/api';

interface RiskMetrics {
  total_exposure: number;
  max_drawdown: number;
  var_1d: number;
  position_count: number;
}

interface RiskLimits {
  max_position_size: number;
  max_daily_loss: number;
  max_drawdown_pct: number;
  max_leverage: number;
  max_orders_per_day: number;
}

type RiskMetricsResponse = Partial<RiskMetrics> & {
  var_95?: number;
  open_positions?: number;
};

type RiskLimitsResponse = Partial<RiskLimits>;

const DEFAULT_RISK_LIMITS: RiskLimits = {
  max_position_size: 100000,
  max_daily_loss: 5000,
  max_drawdown_pct: 15,
  max_leverage: 10,
  max_orders_per_day: 1000,
};

function finiteNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function normalizeMetrics(data: RiskMetricsResponse): RiskMetrics {
  return {
    total_exposure: finiteNumber(data.total_exposure),
    max_drawdown: finiteNumber(data.max_drawdown),
    var_1d: finiteNumber(data.var_1d ?? data.var_95),
    position_count: finiteNumber(data.position_count ?? data.open_positions),
  };
}

function normalizeLimits(data: RiskLimitsResponse): RiskLimits {
  return {
    max_position_size: finiteNumber(data.max_position_size, DEFAULT_RISK_LIMITS.max_position_size),
    max_daily_loss: finiteNumber(data.max_daily_loss, DEFAULT_RISK_LIMITS.max_daily_loss),
    max_drawdown_pct: finiteNumber(data.max_drawdown_pct, DEFAULT_RISK_LIMITS.max_drawdown_pct),
    max_leverage: finiteNumber(data.max_leverage, DEFAULT_RISK_LIMITS.max_leverage),
    max_orders_per_day: finiteNumber(data.max_orders_per_day, DEFAULT_RISK_LIMITS.max_orders_per_day),
  };
}

function formatNumber(value: number | null | undefined, digits = 2): string {
  return finiteNumber(value).toFixed(digits);
}

export default function RiskPage() {
  const [metrics, setMetrics] = useState<RiskMetrics | null>(null);
  const [limits, setLimits] = useState<RiskLimits | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [editingLimits, setEditingLimits] = useState(false);
  const [newLimits, setNewLimits] = useState<RiskLimits>(DEFAULT_RISK_LIMITS);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, []);

  const fetchData = async () => {
    try {
      setFetchError(null);
      const [metricsData, limitsData] = await Promise.all([
        api.get<RiskMetricsResponse>('/api/risk/metrics'),
        api.get<RiskLimitsResponse>('/api/risk/limits'),
      ]);
      const normalizedLimits = normalizeLimits(limitsData);
      setMetrics(normalizeMetrics(metricsData));
      setLimits(normalizedLimits);
      setNewLimits(normalizedLimits);
    } catch (error) {
      setFetchError(error instanceof Error ? error.message : 'Failed to load risk data');
    } finally {
      setLoading(false);
    }
  };

  const handleUpdateLimits = async () => {
    try {
      await api.post('/api/risk/limits', newLimits);
      setEditingLimits(false);
      fetchData();
    } catch (error) {
      setFetchError(error instanceof Error ? error.message : 'Failed to update limits');
    }
  };

  const getExposurePercentage = () => {
    if (!metrics || !limits || limits.max_position_size <= 0) return 0;
    return (metrics.total_exposure / limits.max_position_size) * 100;
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 p-8">
        <div className="text-center">Loading risk data...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 p-8">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex justify-between items-center mb-8">
          <div>
            <h1 className="text-4xl font-bold text-gray-900 mb-2">🛡️ Risk Management</h1>
            <p className="text-gray-600">Monitor and control trading risk</p>
          </div>
          <button
            onClick={() => window.location.href = '/trader'}
            className="px-6 py-3 bg-white border-2 border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 transition-all font-semibold"
          >
            ← Back to Dashboard
          </button>
        </div>

        {fetchError && (
          <div className="mb-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {fetchError}
          </div>
        )}

        {/* Risk Metrics */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
          <div className="bg-white rounded-xl shadow-lg p-6">
            <div className="flex items-center justify-between mb-4">
              <div className="text-sm text-gray-500">Total Exposure</div>
              <div className="text-2xl">💰</div>
            </div>
            <div className="text-3xl font-bold text-gray-900 mb-2">
              ${formatNumber(metrics?.total_exposure)}
            </div>
            <div className="w-full bg-gray-200 rounded-full h-2">
              <div 
                className="bg-blue-600 h-2 rounded-full transition-all"
                style={{ width: `${Math.min(getExposurePercentage(), 100)}%` }}
              />
            </div>
            <div className="text-xs text-gray-500 mt-1">
              {getExposurePercentage().toFixed(1)}% of max position size
            </div>
          </div>

          <div className="bg-white rounded-xl shadow-lg p-6">
            <div className="flex items-center justify-between mb-4">
              <div className="text-sm text-gray-500">Open Positions</div>
              <div className="text-2xl">📊</div>
            </div>
            <div className="text-3xl font-bold text-gray-900 mb-2">
              {formatNumber(metrics?.position_count, 0)}
            </div>
            <div className="w-full bg-gray-200 rounded-full h-2">
              <div 
                className="h-2 rounded-full bg-green-600 transition-all"
                style={{ width: `${Math.min(getExposurePercentage(), 100)}%` }}
              />
            </div>
            <div className="text-xs text-gray-500 mt-1">
              Exposure uses {getExposurePercentage().toFixed(1)}% of the position limit
            </div>
          </div>

          <div className="bg-white rounded-xl shadow-lg p-6">
            <div className="flex items-center justify-between mb-4">
              <div className="text-sm text-gray-500">Max Drawdown</div>
              <div className="text-2xl">📉</div>
            </div>
            <div className="text-3xl font-bold text-red-600 mb-2">
              {formatNumber(metrics?.max_drawdown)}%
            </div>
            <div className="text-xs text-gray-500 mt-1">
              VaR (1D, 95%): ${formatNumber(metrics?.var_1d)}
            </div>
          </div>
        </div>

        {/* Risk Limits */}
        <div className="bg-white rounded-xl shadow-lg p-8 mb-8">
          <div className="flex justify-between items-center mb-6">
            <h2 className="text-2xl font-bold text-gray-900">Risk Limits</h2>
            {!editingLimits ? (
              <button
                onClick={() => setEditingLimits(true)}
                disabled={!limits}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-all font-semibold disabled:cursor-not-allowed disabled:opacity-50"
              >
                ✏️ Edit Limits
              </button>
            ) : (
              <div className="flex gap-2">
                <button
                  onClick={() => {
                    setEditingLimits(false);
                    setNewLimits(limits ?? DEFAULT_RISK_LIMITS);
                  }}
                  className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 transition-all font-semibold"
                >
                  Cancel
                </button>
                <button
                  onClick={handleUpdateLimits}
                  className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-all font-semibold"
                >
                  💾 Save
                </button>
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">
                Max Position Size ($)
              </label>
              {editingLimits ? (
                <input
                  type="number"
                  value={newLimits.max_position_size}
                  onChange={(e) => setNewLimits({ ...newLimits, max_position_size: Number(e.target.value) })}
                  className="w-full px-4 py-2 border-2 border-gray-300 rounded-lg focus:border-blue-500 focus:outline-none"
                />
              ) : (
                <div className="text-2xl font-bold text-gray-900">
                  ${formatNumber(limits?.max_position_size)}
                </div>
              )}
            </div>

            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">
                Max Daily Loss ($)
              </label>
              {editingLimits ? (
                <input
                  type="number"
                  value={newLimits.max_daily_loss}
                  onChange={(e) => setNewLimits({ ...newLimits, max_daily_loss: Number(e.target.value) })}
                  className="w-full px-4 py-2 border-2 border-gray-300 rounded-lg focus:border-blue-500 focus:outline-none"
                />
              ) : (
                <div className="text-2xl font-bold text-gray-900">
                  ${formatNumber(limits?.max_daily_loss)}
                </div>
              )}
            </div>

            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">
                Max Drawdown (%)
              </label>
              {editingLimits ? (
                <input
                  type="number"
                  value={newLimits.max_drawdown_pct}
                  onChange={(e) => setNewLimits({ ...newLimits, max_drawdown_pct: Number(e.target.value) })}
                  className="w-full px-4 py-2 border-2 border-gray-300 rounded-lg focus:border-blue-500 focus:outline-none"
                />
              ) : (
                <div className="text-2xl font-bold text-gray-900">
                  {formatNumber(limits?.max_drawdown_pct, 1)}%
                </div>
              )}
            </div>

            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">
                Max Leverage
              </label>
              {editingLimits ? (
                <input
                  type="number"
                  value={newLimits.max_leverage}
                  onChange={(e) => setNewLimits({ ...newLimits, max_leverage: Number(e.target.value) })}
                  className="w-full px-4 py-2 border-2 border-gray-300 rounded-lg focus:border-blue-500 focus:outline-none"
                />
              ) : (
                <div className="text-2xl font-bold text-gray-900">
                  {formatNumber(limits?.max_leverage, 1)}x
                </div>
              )}
            </div>

            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">
                Max Orders Per Day
              </label>
              {editingLimits ? (
                <input
                  type="number"
                  value={newLimits.max_orders_per_day}
                  onChange={(e) => setNewLimits({ ...newLimits, max_orders_per_day: Number(e.target.value) })}
                  className="w-full px-4 py-2 border-2 border-gray-300 rounded-lg focus:border-blue-500 focus:outline-none"
                />
              ) : (
                <div className="text-2xl font-bold text-gray-900">
                  {formatNumber(limits?.max_orders_per_day, 0)}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Risk Alerts */}
        <div className="bg-white rounded-xl shadow-lg p-8">
          <h2 className="text-2xl font-bold text-gray-900 mb-6">Risk Alerts</h2>
          
          <div className="space-y-4">
            {getExposurePercentage() > 90 && (
              <div className="bg-red-50 border-l-4 border-red-600 p-4 rounded">
                <div className="flex items-center">
                  <div className="text-2xl mr-3">🚨</div>
                  <div>
                    <div className="font-bold text-red-900">Critical Exposure Level</div>
                    <div className="text-sm text-red-700">
                      Total exposure is above 90% of maximum. Immediate action required.
                    </div>
                  </div>
                </div>
              </div>
            )}

            {getExposurePercentage() < 50 && (
              <div className="bg-green-50 border-l-4 border-green-600 p-4 rounded">
                <div className="flex items-center">
                  <div className="text-2xl mr-3">✅</div>
                  <div>
                    <div className="font-bold text-green-900">All Systems Normal</div>
                    <div className="text-sm text-green-700">
                      Risk metrics are within acceptable ranges.
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

