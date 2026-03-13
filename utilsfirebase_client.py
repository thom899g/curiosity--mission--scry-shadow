"""
Firebase Firestore client with robust error handling and reconnection logic
Architectural Choice: Singleton pattern ensures single connection pool
"""
import os
import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from dataclasses import dataclass, asdict
from enum import Enum

import firebase_admin
from firebase_admin import credentials, firestore
from firebase_admin.exceptions import FirebaseError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class Collection(Enum):
    """Firestore collections for structured data access"""
    PROBES = "probes"
    REVENUE_EVENTS = "revenue_events"
    THREAT_SCENARIOS = "threat_scenarios"
    CIRCUIT_BREAKERS = "circuit_breakers"
    FUNDING_THESIS = "funding_thesis"
    SYSTEM_LOGS = "system_logs"
    AMPLIFICATION_MODELS = "amplification_models"

@dataclass
class FirestoreConfig:
    """Configuration for Firebase connection"""
    credential_path: str
    project_id: str
    max_retries: int = 3
    timeout_seconds: int = 30

class FirebaseClient:
    """Singleton Firestore client with connection pooling and error recovery"""
    
    _instance = None
    _client = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self._config: Optional[FirestoreConfig] = None
            self._initialized = True
    
    def initialize(self, config: FirestoreConfig) -> bool:
        """
        Initialize Firebase connection with retry logic
        
        Args:
            config: Firestore configuration
            
        Returns:
            bool: True if initialization successful
            
        Raises:
            ValueError: If credential file doesn't exist
            FirebaseError: If Firebase initialization fails
        """
        try:
            # Validate credential file exists
            if not os.path.exists(config.credential_path):
                logger.error(f"Credential file not found: {config.credential_path}")
                raise ValueError(f"Credential file not found: {config.credential_path}")
            
            self._config = config
            
            # Initialize Firebase app if not already initialized
            if not firebase_admin._apps:
                cred = credentials.Certificate(config.credential_path)
                firebase_admin.initialize_app(cred, {
                    'projectId': config.project_id
                })
            
            # Test connection with retry logic
            for attempt in range(config.max_retries):
                try:
                    self._client = firestore.client()
                    # Test connection with a simple operation
                    self._client.collection('test').limit(1).get()
                    logger.info(f"Firestore connection established (attempt {attempt + 1})")
                    return True
                    
                except FirebaseError as e:
                    logger.warning(f"Firebase connection attempt {attempt + 1} failed: {str(e)}")
                    if attempt == config.max_retries - 1:
                        logger.error(f"Failed to connect to Firestore after {config.max_retries} attempts")
                        raise
            
            return False
            
        except Exception as e:
            logger.error(f"Firebase initialization failed: {str(e)}")
            self._client = None
            raise
    
    def get_client(self):
        """Get Firestore client with connection validation"""
        if self._client is None:
            raise ConnectionError("Firebase client not initialized. Call initialize() first.")
        return self._client
    
    def write_document(self, collection: Collection, data: Dict[str, Any], 
                      document_id: Optional[str] = None) -> str:
        """
        Write document to Firestore with error handling
        
        Args:
            collection: Target collection
            data: Document data
            document_id: Optional specific document ID
            
        Returns:
            str: Document ID
            
        Raises:
            FirebaseError: If write operation fails
        """
        try:
            client = self.get_client()
            col_ref = client.collection(collection.value)
            
            # Add timestamp if not present
            if 'timestamp' not in data:
                data['timestamp'] = firestore.SERVER_TIMESTAMP
            
            if 'created_at' not in data:
                data['created_at'] = datetime.utcnow().isoformat()
            
            if document_id:
                doc_ref = col_ref.document(document_id)
                doc_ref.set(data, merge=True)
                logger.info(f"Document written to {collection.value}/{document_id}")
            else:
                doc_ref = col_ref.add(data)[1]
                logger.info(f"Document written to {collection.value}/{doc_ref.id}")
            
            return doc_ref.id if document_id is None else document_id
            
        except FirebaseError as e:
            logger.error(f"Firestore write failed for {collection.value}: {str(e)}")
            # Log error to system logs for audit
            self._log_error("firestore_write", str(e), {"collection": collection.value})
            raise
    
    def read_document(self, collection: Collection, document_id: str) -> Optional[Dict[str, Any]]:
        """Read document with error handling"""
        try:
            client = self.get_client()
            doc_ref = client.collection(collection.value).document(document_id)
            doc = doc_ref.get()
            
            if doc.exists:
                data = doc.to_dict()
                logger.debug(f"Read document from {collection.value}/{document_id}")
                return data
            else:
                logger.warning(f"Document {document_id} not found in {collection.value}")
                return None
                
        except FirebaseError as e:
            logger.error(f"Firestore read failed: {str(e)}")
            return None
    
    def query_collection(self, collection: Collection, 
                        filters: Optional[List[tuple]] = None,
                        order_by: Optional[str] = None,
                        limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Query collection with filters"""
        try:
            client = self.get_client()
            col_ref = client.collection(collection.value)
            
            # Apply filters
            if filters:
                for field, op, value in filters:
                    col_ref = col_ref.where(field, op, value)
            
            # Apply ordering
            if order_by:
                col_ref = col_ref.order_by(order_by)
            
            # Apply limit
            if limit:
                col_ref = col_ref.limit(limit)
            
            docs = col_ref.stream()
            results = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                results.append(data)
            
            logger.debug(f"Query executed on {collection.value}, returned {len(results)} documents")
            return results
            
        except FirebaseError as e:
            logger.error(f"Firestore query failed: {str(e)}")
            return []
    
    def _log_error(self, error_type: str, error_message: str, context: Dict[str, Any]):
        """Internal method to log errors to system logs"""
        try:
            error_data = {
                'type': error_type,
                'message': error_message,
                'context': context,
                'timestamp': firestore.SERVER_TIMESTAMP,
                'severity': 'ERROR'
            }
            client = self.get_client()
            client.collection(Collection.SYSTEM_LOGS.value).add(error_data)
        except Exception as e:
            logger.error(f"Failed to log error to Firestore: {str(e)}")
    
    def close(self):
        """Close Firebase connection"""
        try:
            if firebase_admin._apps:
                firebase_admin.delete_app(firebase_admin.get_app())
                self._client = None
                logger.info("Firebase connection closed")
        except Exception as e:
            logger.error(f"Error closing Firebase connection: {str(e)}")