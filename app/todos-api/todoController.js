'use strict';
const { promisify } = require('util');
const {Annotation,
    jsonEncoder: {JSON_V2}} = require('zipkin');

// [ADDED] Elastic APM for Node.js
const apm = require('elastic-apm-node');

const OPERATION_CREATE = 'CREATE',
      OPERATION_DELETE = 'DELETE';

class TodoController {
    constructor({tracer, redisClient, logChannel}) {
        this._tracer = tracer;
        this._redisClient = redisClient;
        this._logChannel = logChannel;
        // Promisify Redis GET/SET so todos persist across pod restarts via Redis AOF.
        // PUBLISH (used in _logOperation) stays callback-based — the same client handles both.
        this._redisGet = promisify(redisClient.get).bind(redisClient);
        this._redisSet = promisify(redisClient.set).bind(redisClient);
    }

    async list(req, res) {
        const transaction = apm.currentTransaction;
        if (transaction) {
            transaction.setLabel('username', req.user.username);
            transaction.setLabel('operation', 'LIST');
            transaction.setLabel('timestamp', Date.now());
        }

        try {
            const data = await this._getTodoData(req.user.username);

            const span = apm.startSpan('redis_get_todos', 'db');
            if (span) {
                span.setLabel('todo_count', Object.keys(data.items).length);
            }
            res.json(data.items);
            if (span) span.end();
        } catch (error) {
            apm.captureError(error);
            res.status(500).json({error: 'Failed to list todos'});
        }
    }

    async create(req, res) {
        const transaction = apm.currentTransaction;
        if (transaction) {
            transaction.setLabel('username', req.user.username);
            transaction.setLabel('operation', 'CREATE');
            transaction.setLabel('timestamp', Date.now());
            transaction.setLabel('todo_content', req.body.content);
        }

        try {
            const data = await this._getTodoData(req.user.username);
            const todo = {
                content: req.body.content,
                id: data.lastInsertedID
            };
            data.items[data.lastInsertedID] = todo;
            data.lastInsertedID++;

            const dbSpan = apm.startSpan('redis_set_todos', 'db');
            await this._setTodoData(req.user.username, data);
            if (dbSpan) dbSpan.end();

            const logSpan = apm.startSpan('log_operation', 'messaging');
            this._logOperation(OPERATION_CREATE, req.user.username, todo.id);
            if (logSpan) logSpan.end();

            if (transaction) {
                transaction.setLabel('todo_id', todo.id);
                transaction.setLabel('success', true);
                transaction.outcome = 'success';
            }

            res.json(todo);
        } catch (error) {
            if (transaction) {
                transaction.setLabel('error', error.message);
                transaction.setLabel('success', false);
                transaction.outcome = 'failure';
            }
            apm.captureError(error);
            res.status(500).json({error: 'Failed to create todo'});
        }
    }

    async delete(req, res) {
        const transaction = apm.currentTransaction;
        if (transaction) {
            transaction.setLabel('username', req.user.username);
            transaction.setLabel('operation', 'DELETE');
            transaction.setLabel('timestamp', Date.now());
            transaction.setLabel('todo_id', req.params.taskId);
        }

        try {
            const data = await this._getTodoData(req.user.username);
            const id = req.params.taskId;

            const deletedTodo = data.items[id];
            delete data.items[id];

            const dbSpan = apm.startSpan('redis_set_todos', 'db');
            await this._setTodoData(req.user.username, data);
            if (dbSpan) dbSpan.end();

            const logSpan = apm.startSpan('log_operation', 'messaging');
            this._logOperation(OPERATION_DELETE, req.user.username, id);
            if (logSpan) logSpan.end();

            if (transaction) {
                transaction.setLabel('deleted_todo', deletedTodo ? deletedTodo.content : 'not_found');
                transaction.setLabel('success', true);
                transaction.outcome = 'success';
            }

            res.status(204).send();
        } catch (error) {
            if (transaction) {
                transaction.setLabel('error', error.message);
                transaction.setLabel('success', false);
                transaction.outcome = 'failure';
            }
            apm.captureError(error);
            res.status(500).json({error: 'Failed to delete todo'});
        }
    }

    _logOperation (opName, username, todoData) {
        this._tracer.scoped(() => {
            const traceId = this._tracer.id;
            const span = apm.startSpan('redis_publish', 'messaging');
            if (span) {
                span.setLabel('operation', opName);
                span.setLabel('username', username);
                span.setLabel('todo_id', todoData);
            }

            try {
                this._redisClient.publish(this._logChannel, JSON.stringify({
                    zipkinSpan: traceId,
                    opName: opName,
                    username: username,
                    todo: todoData,
                    timestamp: Date.now()
                }));
                if (span) span.setLabel('success', true);
                console.log(`TODO_OPERATION_LOGGED: operation=${opName} username=${username} todo_id=${todoData}`);
            } catch (error) {
                if (span) {
                    span.setLabel('error', error.message);
                    span.setLabel('success', false);
                }
                apm.captureError(error);
                console.error(`Failed to publish todo operation: ${error.message}`);
            } finally {
                if (span) span.end();
            }
        });
    }

    async _getTodoData (userID) {
        const raw = await this._redisGet(`todos:${userID}`);
        if (raw === null) {
            // First visit — seed default todos and persist them immediately.
            // lastInsertedID starts at 4 so new todos don't overwrite the defaults (IDs 1-3).
            const defaultData = {
                items: {
                    '1': { id: 1, content: "Create new todo" },
                    '2': { id: 2, content: "Update me" },
                    '3': { id: 3, content: "Delete example ones" }
                },
                lastInsertedID: 4
            };
            await this._redisSet(`todos:${userID}`, JSON.stringify(defaultData));
            return defaultData;
        }
        return JSON.parse(raw);
    }

    async _setTodoData (userID, data) {
        await this._redisSet(`todos:${userID}`, JSON.stringify(data));
    }
}

module.exports = TodoController;
