"""
Genesis Engine - Intelligent Probe Deployment and Management
Architectural Choice: Event-driven probe activation with A/B testing capabilities
"""
import logging
import time
import json
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from enum import Enum
import random

import requests
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split

from utils.firebase_client import FirebaseClient, Collection

logger = logging.getLogger(__name__)

class ProbeType(Enum):
    """Types of revenue discovery probes"""
    API_ENDPOINT = "api_endpoint"
    LOG_ANALYSIS = "log_analysis"
    TRANSACTION_MONITOR = "transaction_monitor"
    CUSTOM_SCRIPT = "custom_script"

class ProbeStatus(Enum):
    """Probe lifecycle status"""
    ACTIVE = "active"
    PAUSED = "paused"
    FAILED = "failed"
    AMPLIFIED = "amplified"
    DEPRECATED = "deprecated"

class ProbeManager:
    """Manages intelligent revenue discovery probes with A/B testing"""
    
    def __init__(self, firebase_client: FirebaseClient):
        """
        Initialize Probe Manager
        
        Args:
            firebase_client: Initialized Firebase client
        """
        self.firebase = firebase_client
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Project-Cerebellum/1.0',
            'Accept': 'application/json'
        })
        self.active_probes: Dict[str, Dict] = {}
        self.amplification_model: Optional[RandomForestRegressor] = None
        
    def deploy_probe(self, probe_config: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Deploy a new revenue discovery probe
        
        Args:
            probe_config: Probe configuration
            
        Returns:
            Tuple of (success, probe_id or error_message)
        """
        try:
            # Validate required fields
            required_fields = ['name', 'type', 'target', 'extraction_pattern']
            for field in required_fields:
                if field not in probe_config:
                    return False, f"Missing required field: {field}"
            
            # Set default values
            probe_config.setdefault('status', ProbeStatus.ACTIVE.value)
            probe_config.setdefault('created_at', datetime.utcnow().isoformat())
            probe_config.setdefault('stimulus_parameters', {})
            probe_config.setdefault('amplification_score', 0.0)
            probe_config.setdefault('last_executed', None)
            probe_config.setdefault('execution_count', 0)
            probe_config.setdefault('success_count', 0)
            
            # Generate probe ID
            probe_id = f"probe_{int(time.time())}_{random.randint(1000, 9999)}"
            
            # Store in Firestore
            self.firebase.write_document(
                Collection.PROBES,
                probe_config,
                probe_id
            )
            
            # Cache in memory
            self.active_probes[probe_id] = probe_config
            
            logger.info(f"Probe deployed: {probe_id} - {probe_config['name']}")
            return True, probe_id
            
        except Exception as e:
            error_msg = f"Failed to deploy probe: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    def execute_probe(self, probe_id: str, apply_stimulus: bool = True) -> Optional[Dict[str, Any]]:
        """
        Execute a probe and capture revenue events
        
        Args:
            probe_id: ID of probe to execute
            apply_stimulus: Whether to apply A/B testing stimulus
            
        Returns:
            Revenue event data or None if failed
        """
        try:
            # Get probe configuration
            probe_data = self.firebase.read_document(Collection.PROBES, probe_id)
            if not probe_data:
                logger.error(f"Probe not found: {probe_id}")
                return None
            
            # Update execution metadata
            probe_data['last_executed'] = datetime.utcnow().isoformat()
            probe_data['execution_count'] = probe_data.get('execution_count', 0) + 1
            
            # Apply stimulus for A/B testing if enabled
            stimulus_applied = False
            if apply_stimulus and probe_data.get('stimulus_parameters'):
                stimulus_applied = self._apply_stimulus(probe_data)
            
            # Execute based on probe type
            revenue_data = None
            probe_type = probe_data.get('type')
            
            if probe_type == ProbeType.API_ENDPOINT.value:
                revenue_data = self._execute_api_probe(probe_data)
            elif probe_type == ProbeType.LOG_ANALYSIS.value:
                revenue_data = self._execute_log_probe(probe_data)
            elif probe_type == ProbeType.TRANSACTION_MONITOR.value:
                revenue_data = self._execute_transaction_probe(probe_data)
            elif probe_type == ProbeType.CUSTOM_SCRIPT.value:
                revenue_data = self._execute_custom_probe(probe_data)
            else:
                logger.error(f"Unknown probe type: {probe_type}")
                return None
            
            if revenue_data:
                # Add probe metadata
                revenue_data['probe_id'] = probe_id
                revenue_data['stimulus_applied'] = stimulus_applied
                revenue_data['probe_type'] = probe_type
                
                # Store revenue event
                event_id = self.firebase.write_document(
                    Collection.REVENUE_EVENTS,
                    revenue_data
                )
                
                # Update probe success count
                probe_data['success_count'] = probe_data.get('success_count', 0) + 1
                
                # Calculate amplification if stimulus was applied
                if stimulus_applied:
                    self._calculate_amplification(probe_id, revenue_data)
                
                # Update probe in Firestore
                self.firebase.write_document(
                    Collection.PROBES,
                    probe_data,
                    probe_id
                )
                
                logger.info(f"Probe {probe_id} executed successfully, event: {event_id}")
                return revenue_data
            
            else:
                probe_data['status'] = ProbeStatus.FAILED.value
                self.firebase.write_document(Collection.PROBES, probe_data, probe_id)
                logger.warning(f"Probe {probe_id} execution failed")
                return None
                
        except Exception as e:
            logger.error(f"Error executing probe {probe_id}: