const DB_NAME = "seco";

const dbRef = db.getSiblingDB(DB_NAME);

print("\n========================================");
print(" SECO MongoDB Setup");
print(" Database: " + DB_NAME);
print("========================================\n");


function collectionExists(name) {
  return dbRef.getCollectionNames().includes(name);
}


function createOrUpdateCollection(name, validator) {
  if (!collectionExists(name)) {
    dbRef.createCollection(name, {
      validator: {
        $jsonSchema: validator
      },
      validationLevel: "strict",
      validationAction: "error"
    });

    print("[CREATE] " + name);
  } else {
    dbRef.runCommand({
      collMod: name,
      validator: {
        $jsonSchema: validator
      },
      validationLevel: "strict",
      validationAction: "error"
    });

    print("[UPDATE] " + name);
  }
}


/* =========================================================
   1. USERS
   ========================================================= */

createOrUpdateCollection("users", {
  bsonType: "object",
  required: [
    "user_id",
    "name",
    "email",
    "password_hash",
    "role",
    "balance_vnd",
    "is_active",
    "created_at",
    "updated_at"
  ],
  properties: {
    user_id: {
      bsonType: "string"
    },

    name: {
      bsonType: "string",
      minLength: 1
    },

    email: {
      bsonType: "string",
      minLength: 3
    },

    password_hash: {
      bsonType: "string",
      minLength: 1
    },

    role: {
      enum: [
        "USER",
        "ADMIN",
        "TECHNICIAN"
      ]
    },

    balance_vnd: {
      bsonType: [
        "int",
        "long"
      ],
      minimum: 0
    },

    is_active: {
      bsonType: "bool"
    },

    created_at: {
      bsonType: "date"
    },

    updated_at: {
      bsonType: "date"
    }
  }
});


/* =========================================================
   2. RFID CARDS
   ========================================================= */

createOrUpdateCollection("rfid_cards", {
  bsonType: "object",
  required: [
    "card_id",
    "user_id",
    "is_active",
    "pending",
    "created_at",
    "updated_at"
  ],
  properties: {
    card_id: {
      bsonType: "string"
    },

    uid: {
      bsonType: [
        "string",
        "null"
      ]
    },

    user_id: {
      bsonType: "string"
    },

    is_active: {
      bsonType: "bool"
    },

    pending: {
      bsonType: "bool"
    },

    created_at: {
      bsonType: "date"
    },

    updated_at: {
      bsonType: "date"
    }
  }
});


/* =========================================================
   3. CHARGING STATIONS
   ========================================================= */

createOrUpdateCollection("charging_stations", {
  bsonType: "object",
  required: [
    "station_id",
    "name",
    "status",
    "is_online",
    "connector_lock",
    "current_session_id",
    "created_at",
    "updated_at"
  ],
  properties: {
    station_id: {
      bsonType: "string"
    },

    name: {
      bsonType: "string",
      minLength: 1
    },

    status: {
      enum: [
        "READY",
        "CHARGING",
        "ERROR",
        "MAINTENANCE"
      ]
    },

    is_online: {
      bsonType: "bool"
    },

    connector_lock: {
      enum: [
        "UNLOCKED",
        "LOCKED",
        "UNKNOWN"
      ]
    },

    current_session_id: {
      bsonType: [
        "string",
        "null"
      ]
    },

    last_heartbeat_at: {
      bsonType: [
        "date",
        "null"
      ]
    },

    created_at: {
      bsonType: "date"
    },

    updated_at: {
      bsonType: "date"
    }
  }
});


/* =========================================================
   4. CHARGING SESSIONS
   ========================================================= */

createOrUpdateCollection("charging_sessions", {
  bsonType: "object",
  required: [
    "session_id",
    "station_id",
    "type",
    "status",
    "energy_kwh",
    "power_kw",
    "price_per_kwh_vnd",
    "cost_vnd",
    "started_at",
    "ended_at",
    "connector_locked",
    "created_at",
    "updated_at"
  ],
  properties: {
    session_id: {
      bsonType: "string"
    },

    station_id: {
      bsonType: "string"
    },

    type: {
      enum: [
        "MEMBER",
        "GUEST"
      ]
    },

    user_id: {
      bsonType: [
        "string",
        "null"
      ]
    },

    status: {
      enum: [
        "ACTIVE",
        "WAITING_PAYMENT",
        "COMPLETED",
        "ERROR"
      ]
    },

    max_budget_vnd: {
      bsonType: [
        "int",
        "long",
        "null"
      ],
      minimum: 0
    },

    energy_kwh: {
      bsonType: "decimal",
      minimum: 0
    },

    power_kw: {
      bsonType: "decimal",
      minimum: 0
    },

    price_per_kwh_vnd: {
      bsonType: [
        "int",
        "long"
      ],
      minimum: 0
    },

    cost_vnd: {
      bsonType: [
        "int",
        "long"
      ],
      minimum: 0
    },

    stop_reason: {
      bsonType: ["string", "null"],
      enum: [
        "UNPLUG",
        "MAX_BUDGET",
        "INSUFFICIENT_BALANCE",
        "TECHNICIAN",
        "OVER_TEMPERATURE",
        "E_STOP",
        "SYSTEM_ERROR",
        null
      ]
    },

    started_at: {
      bsonType: "date"
    },

    ended_at: {
      bsonType: [
        "date",
        "null"
      ]
    },

    connector_locked: {
      bsonType: "bool"
    },

    created_at: {
      bsonType: "date"
    },

    updated_at: {
      bsonType: "date"
    }
  }
});


/* =========================================================
   5. TRANSACTIONS
   ========================================================= */

createOrUpdateCollection("transactions", {
  bsonType: "object",
  required: [
    "transaction_id",
    "session_id",
    "type",
    "amount_vnd",
    "status",
    "created_at"
  ],
  properties: {
    transaction_id: {
      bsonType: "string"
    },

    session_id: {
      bsonType: "string"
    },

    user_id: {
      bsonType: [
        "string",
        "null"
      ]
    },

    type: {
      enum: [
        "CHARGING",
        "GUEST_PAYMENT"
      ]
    },

    amount_vnd: {
      bsonType: [
        "int",
        "long"
      ],
      minimum: 0
    },

    balance_before_vnd: {
      bsonType: [
        "int",
        "long",
        "null"
      ],
      minimum: 0
    },

    balance_after_vnd: {
      bsonType: [
        "int",
        "long",
        "null"
      ],
      minimum: 0
    },

    status: {
      enum: [
        "COMPLETED",
        "FAILED"
      ]
    },

    created_at: {
      bsonType: "date"
    }
  }
});


/* =========================================================
   6. PAYMENT INTENTS
   ========================================================= */

createOrUpdateCollection("payment_intents", {
  bsonType: "object",
  required: [
    "payment_id",
    "session_id",
    "amount_vnd",
    "status",
    "provider",
    "qr_payload",
    "created_at",
    "expires_at",
    "paid_at",
    "updated_at"
  ],
  properties: {
    payment_id: {
      bsonType: "string"
    },

    session_id: {
      bsonType: "string"
    },

    amount_vnd: {
      bsonType: [
        "int",
        "long"
      ],
      minimum: 0
    },

    status: {
      enum: [
        "PENDING",
        "PAID",
        "EXPIRED",
        "CANCELLED"
      ]
    },

    provider: {
      enum: [
        "SANDBOX"
      ]
    },

    qr_payload: {
      bsonType: "string"
    },

    created_at: {
      bsonType: "date"
    },

    expires_at: {
      bsonType: "date"
    },

    paid_at: {
      bsonType: [
        "date",
        "null"
      ]
    },

    updated_at: {
      bsonType: "date"
    }
  }
});


/* =========================================================
   7. WALLET HOLDS
   ========================================================= */

createOrUpdateCollection("wallet_holds", {
  bsonType: "object",
  required: [
    "wallet_hold_id",
    "user_id",
    "session_id",
    "amount_vnd",
    "status",
    "created_at"
  ],
  properties: {
    wallet_hold_id: {
      bsonType: "string"
    },

    user_id: {
      bsonType: "string"
    },

    session_id: {
      bsonType: "string"
    },

    amount_vnd: {
      bsonType: [
        "int",
        "long"
      ],
      minimum: 0
    },

    status: {
      enum: [
        "HOLDING",
        "RELEASED",
        "CANCELLED"
      ]
    },

    created_at: {
      bsonType: "date"
    },

    released_at: {
      bsonType: [
        "date",
        "null"
      ]
    }
  }
});


/* =========================================================
   8. MAINTENANCE RECORDS
   ========================================================= */

createOrUpdateCollection("maintenance_records", {
  bsonType: "object",
  required: [
    "maintenance_id",
    "station_id",
    "technician_id",
    "status",
    "started_at",
    "completed_at",
    "description",
    "created_at",
    "updated_at"
  ],
  properties: {
    maintenance_id: {
      bsonType: "string"
    },

    station_id: {
      bsonType: "string"
    },

    technician_id: {
      bsonType: "string"
    },

    error_id: {
      bsonType: [
        "string",
        "null"
      ]
    },

    status: {
      enum: [
        "IN_PROGRESS",
        "COMPLETED",
        "CANCELLED"
      ]
    },

    started_at: {
      bsonType: "date"
    },

    completed_at: {
      bsonType: [
        "date",
        "null"
      ]
    },

    description: {
      bsonType: "string"
    },

    created_at: {
      bsonType: "date"
    },

    updated_at: {
      bsonType: "date"
    }
  }
});


/* =========================================================
   9. ERROR LOGS
   ========================================================= */

createOrUpdateCollection("error_logs", {
  bsonType: "object",
  required: [
    "error_id",
    "station_id",
    "error_type",
    "severity",
    "message",
    "occurred_at",
    "resolved_at",
    "resolved_by",
    "created_at"
  ],
  properties: {
    error_id: {
      bsonType: "string"
    },

    station_id: {
      bsonType: "string"
    },

    error_type: {
      enum: [
        "OVER_TEMPERATURE",
        "E_STOP",
        "RELAY_FAULT",
        "COMMUNICATION_ERROR",
        "SENSOR_ERROR",
        "SYSTEM_ERROR"
      ]
    },

    severity: {
      enum: [
        "WARNING",
        "ERROR",
        "CRITICAL"
      ]
    },

    message: {
      bsonType: "string"
    },

    occurred_at: {
      bsonType: "date"
    },

    resolved_at: {
      bsonType: [
        "date",
        "null"
      ]
    },

    resolved_by: {
      bsonType: [
        "string",
        "null"
      ]
    },

    created_at: {
      bsonType: "date"
    }
  }
});


/* =========================================================
   10. AUDIT LOGS
   ========================================================= */

createOrUpdateCollection("audit_logs", {
  bsonType: "object",
  required: [
    "audit_id",
    "actor_user_id",
    "actor_role",
    "action",
    "target_type",
    "target_id",
    "created_at"
  ],
  properties: {
    audit_id: {
      bsonType: "string"
    },

    actor_user_id: {
      bsonType: "string"
    },

    actor_role: {
      enum: [
        "USER",
        "ADMIN",
        "TECHNICIAN"
      ]
    },

    action: {
      bsonType: "string"
    },

    target_type: {
      bsonType: "string"
    },

    target_id: {
      bsonType: "string"
    },

    metadata: {
      bsonType: "object"
    },

    created_at: {
      bsonType: "date"
    }
  }
});


/* =========================================================
   INDEXES
   ========================================================= */

print("\nCreating indexes...\n");


/* USERS */

dbRef.users.createIndex(
  { user_id: 1 },
  {
    unique: true,
    name: "users_user_id_unique"
  }
);

dbRef.users.createIndex(
  { email: 1 },
  {
    unique: true,
    name: "users_email_unique"
  }
);


/* RFID */

if (
  dbRef.rfid_cards
    .getIndexes()
    .some(index => index.name === "rfid_uid_unique")
) {
  dbRef.rfid_cards.dropIndex("rfid_uid_unique");
}

dbRef.rfid_cards.createIndex(
  { card_id: 1 },
  {
    unique: true,
    name: "rfid_card_id_unique"
  }
);

dbRef.rfid_cards.createIndex(
  { uid: 1 },
  {
    unique: true,
    name: "rfid_uid_unique",
    partialFilterExpression: {
      uid: {
        $type: "string"
      }
    }
  }
);


/* STATIONS */

dbRef.charging_stations.createIndex(
  { station_id: 1 },
  {
    unique: true,
    name: "stations_id_unique"
  }
);


/* SESSIONS */

dbRef.charging_sessions.createIndex(
  { session_id: 1 },
  {
    unique: true,
    name: "sessions_id_unique"
  }
);

dbRef.charging_sessions.createIndex(
  { station_id: 1, status: 1 },
  {
    name: "sessions_station_status"
  }
);

dbRef.charging_sessions.createIndex(
  { user_id: 1, started_at: -1 },
  {
    name: "sessions_user_started"
  }
);


/*
 * Only one ACTIVE / WAITING_PAYMENT session
 * may exist on one station.
 */

dbRef.charging_sessions.createIndex(
  { station_id: 1 },
  {
    unique: true,
    name: "sessions_one_open_per_station",
    partialFilterExpression: {
      status: {
        $in: [
          "ACTIVE",
          "WAITING_PAYMENT"
        ]
      }
    }
  }
);


/* TRANSACTIONS */

dbRef.transactions.createIndex(
  { session_id: 1, type: 1 },
  {
    unique: true,
    name: "transactions_session_type_unique"
  }
);

dbRef.transactions.createIndex(
  { transaction_id: 1 },
  {
    unique: true,
    name: "transactions_id_unique"
  }
);

dbRef.transactions.createIndex(
  { session_id: 1 },
  {
    name: "transactions_session"
  }
);

dbRef.transactions.createIndex(
  { user_id: 1, created_at: -1 },
  {
    name: "transactions_user_created"
  }
);


/* PAYMENTS */

dbRef.payment_intents.createIndex(
  { payment_id: 1 },
  {
    unique: true,
    name: "payments_id_unique"
  }
);

dbRef.payment_intents.createIndex(
  { session_id: 1 },
  {
    name: "payments_session"
  }
);

dbRef.payment_intents.createIndex(
  { status: 1, created_at: -1 },
  {
    name: "payments_status_created"
  }
);


/* WALLET HOLDS */

dbRef.wallet_holds.createIndex(
  { wallet_hold_id: 1 },
  {
    unique: true,
    name: "wallet_holds_id_unique"
  }
);

dbRef.wallet_holds.createIndex(
  { session_id: 1 },
  {
    unique: true,
    name: "wallet_holds_session_unique"
  }
);


/* MAINTENANCE */

dbRef.maintenance_records.createIndex(
  { maintenance_id: 1 },
  {
    unique: true,
    name: "maintenance_id_unique"
  }
);

dbRef.maintenance_records.createIndex(
  { station_id: 1, started_at: -1 },
  {
    name: "maintenance_station_started"
  }
);


/* ERROR LOGS */

dbRef.error_logs.createIndex(
  { error_id: 1 },
  {
    unique: true,
    name: "errors_id_unique"
  }
);

dbRef.error_logs.createIndex(
  { station_id: 1, occurred_at: -1 },
  {
    name: "errors_station_occurred"
  }
);


/* AUDIT LOGS */

dbRef.audit_logs.createIndex(
  { audit_id: 1 },
  {
    unique: true,
    name: "audit_id_unique"
  }
);

dbRef.audit_logs.createIndex(
  { created_at: -1 },
  {
    name: "audit_created"
  }
);

dbRef.audit_logs.createIndex(
  { actor_user_id: 1, created_at: -1 },
  {
    name: "audit_actor_created"
  }
);


/* =========================================================
   SEED EVCS-01
   ========================================================= */

print("\nSeeding EVCS-01...\n");


dbRef.charging_stations.updateOne(
  {
    station_id: "EVCS-01"
  },
  {
    $set: {
      name: "SECO Station 01",
      status: "READY",
      is_online: false,
      connector_lock: "UNLOCKED",
      current_session_id: null,
      updated_at: new Date()
    },

    $setOnInsert: {
      station_id: "EVCS-01",
      last_heartbeat_at: null,
      created_at: new Date()
    }
  },
  {
    upsert: true
  }
);


/* =========================================================
   FINAL OUTPUT
   ========================================================= */

print("\n========================================");
print(" SECO MongoDB setup completed");
print("========================================\n");

print("Database:");
print(dbRef.getName());

print("\nCollections:");
printjson(dbRef.getCollectionNames());

print("\nEVCS-01:");
printjson(
  dbRef.charging_stations.findOne({
    station_id: "EVCS-01"
  })
);

print("\n========================================\n");