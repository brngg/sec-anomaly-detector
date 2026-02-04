# SEC Filing Project Plan — Week 1

## Strategy & Core Questions

### 1. What companies do we want to track?
- **Decision:** Top 100 companies (S&P 100).
- **Reasoning:** Something manageable dataset for one person that still provides enough consistent filing activity to detect meaningful patterns and anomalies.

### 2. What is the time period/timeframe?
- **Decision:** 6 months of historical data.
- **Reasoning:** A 6-month window because it provides enough data to capture at least two major quarterly filings per company without the operational lag or "data drift" of a 5-year history. It ensures recent baseline while keeping ingestion fast and easy to debug.

### 3. What filings do we want for MVP?
- **Decision:** 10-K, 10-Q, and 8-K.
- **Reasoning:** These seem to be three of the main filing type. 10-K and 10-Q establish structural baseline, while 8-Ks act as the primary triggers for event-based anomalies.

### 4. What data do we want?
- **Decision:** Metadata (size, date, type) and Item Disclosures.
- **Reasoning:** We need to observe the structure and frequency of the data first to identify outliers before moving into deep text analysis.

### 5. Data collection: where do we source?
- **Decision:** SEC EDGAR via `edgartools`.
- **Reasoning:** Provides a reliable, programmatic way to backfill historical data and allows for consistent daily updates to keep the dataset current.

### 6. What type of database do we want?
- **Decision:** Relational SQL (SQLite).
- **Reasoning:** SQLite is serverless and easy to set up for an MVP, but uses standard SQL which allows for a seamless transition to PostgreSQL as the data scales.

---

## Plan of Action (Week 1)

1. **Planning and Database Schema Design:** Finalize table structures and relationships.
2. **Create Database Schema:** Write initialization code to build the tables.
3. **Database Connection:** Set up the project environment to talk to the database file.
4. **Ingestion Script:** Build the logic to backfill history and poll for new daily filings.
5. **Review and Document:** Verify data integrity and document the ingestion process.

---

## Database Schema



### TABLE: Companies
| Column | Type | Description |
| :--- | :--- | :--- |
| **cik** | BIGINT (PK) | Unique SEC identifier |
| **name** | TEXT | Official company name |
| **ticker** | TEXT | Trading ticker (Nullable) |
| **industry** | TEXT | Sector or industry classification |
| **updated_at** | TIMESTAMPTZ | Last metadata update |

### TABLE: Filing_Events
| Column | Type | Description |
| :--- | :--- | :--- |
| **accession_id** | TEXT (PK) | Unique SEC filing identifier |
| **cik** | BIGINT (FK) | Links to Companies(cik) |
| **filing_type** | TEXT | e.g., 8-K, 10-Q, 10-K |
| **filed_at** | TIMESTAMPTZ | Precise acceptance datetime |
| **filing_date** | DATE | Coarse filing date |
| **primary_document** | TEXT | Name of the main document |
| **size_bytes** | BIGINT | File size for outlier detection |
| **created_at** | TIMESTAMPTZ | Internal record insertion time |

### TABLE: Watermarks
| Column | Type | Description |
| :--- | :--- | :--- |
| **cik** | BIGINT (PK) | Links to Companies(cik) |
| **last_seen_filed_at** | TIMESTAMPTZ | Latest filing timestamp ingested |
| **updated_at** | TIMESTAMPTZ | Last time watermark was updated |
| **last_run_at** | TIMESTAMPTZ | Timestamp of last script execution |
| **last_run_status** | TEXT | SUCCESS / FAIL |
| **last_error** | TEXT | Error log if run failed |

### TABLE: Alerts
| Column | Type | Description |
| :--- | :--- | :--- |
| **alert_id** | BIGINT (PK) | Unique alert ID |
| **accession_id** | TEXT (FK) | Links to Filing_Events(accession_id) |
| **anomaly_type** | TEXT | e.g., FREQUENCY_SPIKE, SIZE_OUTLIER |
| **severity_score** | NUMERIC | Ranking from 0.0 to 1.0 |
| **description** | TEXT | Human-readable explanation |
| **details** | JSON | Data payload (math/proof) |
| **status** | TEXT | OPEN / INVESTIGATED / FALSE_POSITIVE |
| **dedupe_key** | TEXT | Prevents duplicate alerts for same event |
| **created_at** | TIMESTAMPTZ | Timestamp of alert generation |