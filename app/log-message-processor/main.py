# MUST be first - elasticapm.instrument() before any other imports!
import elasticapm
import os

# [ADDED] Initialize Elastic APM client for Python
apm_client = elasticapm.Client({
    'SERVICE_NAME': 'log-message-processor',
    'SERVER_URL': os.environ.get('ELASTIC_APM_SERVER_URL', 'http://localhost:8200'),
    'ENVIRONMENT': os.environ.get('ELASTIC_APM_ENVIRONMENT', 'docker'),
    'CENTRAL_CONFIG': False,
})

elasticapm.instrument()

import time
import redis
import json
import requests
from py_zipkin.zipkin import zipkin_span, ZipkinAttrs, generate_random_64bit_string
import random

def log_message(message):
    time_delay = random.randrange(0, 2000)
    time.sleep(time_delay / 1000)
    print('message received after waiting for {}ms: {}'.format(time_delay, message))

if __name__ == '__main__':
    redis_host = os.environ['REDIS_HOST']
    redis_port = int(os.environ['REDIS_PORT'])
    redis_channel = os.environ['REDIS_CHANNEL']
    zipkin_url = os.environ['ZIPKIN_URL'] if 'ZIPKIN_URL' in os.environ else ''
    def http_transport(encoded_span):
        requests.post(
            zipkin_url,
            data=encoded_span,
            headers={'Content-Type': 'application/x-thrift'},
        )

    pubsub = redis.Redis(host=redis_host, port=redis_port, db=0).pubsub()
    pubsub.subscribe([redis_channel])
    print('Log message processor started, listening on channel: {}'.format(redis_channel))
    
    for item in pubsub.listen():
        # [ADDED] Start APM transaction for message processing
        transaction = apm_client.begin_transaction('MessageProcessing')
        
        try:
            # Handle different data types from Redis
            data = item['data']
            if isinstance(data, bytes):
                message = json.loads(data.decode("utf-8"))
            elif isinstance(data, str):
                message = json.loads(data)
            else:
                message = json.loads(str(data))
            
            # Extract operation details from message
            op_name = message.get('opName', 'UNKNOWN')
            username = message.get('username', 'unknown')
            todo_id = message.get('todo', 'unknown')
            
            transaction.labels.update({
                'operation': op_name,
                'username': username,
                'todo_id': todo_id,
                'timestamp': time.time() * 1000
            })
            
        except Exception as e:
            transaction.labels['error'] = str(e)
            log_message(e)
            apm_client.capture_exception()
            transaction.result = 'failure'
            apm_client.end_transaction()
            continue

        if not zipkin_url or 'zipkinSpan' not in message:
            transaction.labels['zipkin_available'] = False
            log_message(message)
            print('LOG_MESSAGE_PROCESSED: operation={} username={} (Zipkin unavailable)'.format(op_name, username))
            transaction.result = 'success'
            apm_client.end_transaction()
            continue

        span_data = message['zipkinSpan']
        try:
            # [ADDED] Create APM span for Zipkin operation
            with apm_client.capture_span('forward_to_zipkin', 'tracing'):
                with zipkin_span(
                    service_name='log-message-processor',
                    zipkin_attrs=ZipkinAttrs(
                        trace_id=span_data['_traceId']['value'],
                        span_id=generate_random_64bit_string(),
                        parent_span_id=span_data['_spanId'],
                        is_sampled=span_data['_sampled']['value'],
                        flags=None
                    ),
                    span_name='save_log',
                    transport_handler=http_transport,
                    sample_rate=100
                ):
                    log_message(message)
                    print('LOG_MESSAGE_PROCESSED: operation={} username={} todo_id={} timestamp={}'.format(
                        op_name, username, todo_id, time.time() * 1000))
            
            transaction.result = 'success'
            transaction.labels['zipkin_sent'] = True
            
        except Exception as e:
            print('did not send data to Zipkin: {}'.format(e))
            transaction.labels['error'] = str(e)
            transaction.labels['zipkin_sent'] = False
            transaction.result = 'failure'
            apm_client.capture_exception()
            log_message(message)
        
        finally:
            apm_client.end_transaction()




