# Authentication Architecture Fixes - Implementation Summary

## Critical Bugs Fixed

### Bug #1: User ID Mismatch (CRITICAL - Would cause complete auth failure)
**Problem:**
- Supabase creates users with UUID (e.g., `abc-123-def`)
- JWT token's `sub` field contains this Supabase UUID
- Backend was creating NEW UUID for users in database
- All queries used `User.id` (our UUID) not the Supabase UUID
- Result: JWT token user_id would NEVER match database records → complete authentication failure

**Solution Implemented:**
- Changed `User.id` to use Supabase UUID directly (no auto-generation)
- Removed `supabase_user_id` field (no longer needed)
- Updated `/initialize` endpoint to use Supabase UUID as primary key
- Now JWT token's `sub` field directly matches `User.id` in database ✅

**Files Modified:**
- `app/db/models.py:46` - Changed User model primary key
- `app/api/routes/users.py:42-50` - Updated user initialization

### Bug #2: No Route Authentication (SECURITY VULNERABILITY)
**Problem:**
- Auth infrastructure existed but wasn't used
- All API endpoints completely open
- Anyone could access/modify any data without authentication

**Solution Implemented:**
- Added `Depends(get_current_user)` to ALL protected endpoints
- Routes now validate JWT tokens and extract user_id
- Only `/initialize` endpoint remains unauthenticated (by design)

**Files Modified:**
- `app/api/routes/groups.py` - All 4 endpoints protected
- `app/api/routes/sessions.py` - All 4 endpoints protected
- `app/api/routes/checkins.py` - All 7 endpoints protected
- `app/api/routes/users.py` - 2 of 3 endpoints protected (/initialize intentionally open)
- `app/api/routes/websocket.py` - WebSocket authentication via JWT query parameter

### Bug #3: User ID in Parameters (IMPERSONATION VULNERABILITY)
**Problem:**
- Routes accepted `user_id` as query/path parameter
- Users could change parameter to impersonate others
- Example: `POST /sessions/end?user_id=ANYONE`

**Solution Implemented:**
- Removed all `user_id` parameters from route signatures
- Extract user_id from JWT token via `Depends(get_current_user)` instead
- User cannot modify their own user_id

**Example Changes:**
```python
# BEFORE (INSECURE):
@router.post("/{session_id}/end")
async def end_session(session_id: str, user_id: str, db: Session = Depends(get_db)):
    # User could pass ANY user_id!

# AFTER (SECURE):
@router.post("/{session_id}/end")
async def end_session(
    session_id: str,
    current_user_id: str = Depends(get_current_user),  # From JWT - cannot be faked
    db: Session = Depends(get_db)
):
```

### Bug #4: Field Name Mismatches
**Problem:**
- Group model uses `partner1_id` and `partner2_id`
- Routes were trying to use `member_a_id` and `member_b_id`
- Schemas had `name` field that doesn't exist in model

**Solution Implemented:**
- Updated `GroupCreate` schema to accept `partner_id` only (current user inferred from JWT)
- Updated `GroupResponse` schema to use `partner1_id`, `partner2_id`, `status`
- Removed non-existent `name` field
- Updated group creation logic to use authenticated user as partner1

**Files Modified:**
- `app/schemas/schemas.py:30-42` - Fixed GroupCreate and GroupResponse schemas
- `app/api/routes/groups.py:22-96` - Updated create_group to use correct field names

## Additional Improvements

### Authorization Checks Added
Routes now verify users can only access their own data:
- `GET /groups/{group_id}` - User must be a member
- `DELETE /groups/{group_id}` - User must be a member
- `GET /sessions/{session_id}` - User must be a participant
- `GET /checkins/{checkin_id}` - User must be assigned_to or verifier

### New Endpoint: GET /users/me
Added convenient endpoint to get current user profile:
```python
@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
```

### WebSocket Authentication
Updated WebSocket to use JWT authentication:
- **Old URL:** `ws://localhost:8000/ws/chat/{session_id}/{user_id}` ❌ (user_id could be faked)
- **New URL:** `ws://localhost:8000/ws/chat/{session_id}?token=JWT_TOKEN` ✅

## Complete List of Modified Files

### Backend Core
1. `app/db/models.py` - User model restructure
2. `app/schemas/schemas.py` - GroupCreate and GroupResponse schemas
3. `app/api/routes/users.py` - User endpoints with auth
4. `app/api/routes/groups.py` - Group endpoints with auth + field name fixes
5. `app/api/routes/sessions.py` - Session endpoints with auth
6. `app/api/routes/checkins.py` - CheckIn endpoints with auth
7. `app/api/routes/websocket.py` - WebSocket JWT authentication

### Auth Infrastructure
- `app/utils/auth.py` - Already correct (verified)
  - `verify_token()` - Validates JWT using Supabase secret
  - `get_user_from_token()` - Extracts `sub` field
  - `get_current_user()` - FastAPI dependency

## Frontend Changes Required

### 1. Update API Configuration
**File:** `third-wheel/src/config/api.js`

The API endpoint paths have changed:
```javascript
// OLD (INSECURE):
ENDPOINTS: {
  GET_USER_GROUPS: (userId) => `/api/groups/user/${userId}`,  // ❌
}

// NEW (SECURE):
ENDPOINTS: {
  GET_MY_GROUPS: '/api/groups/my-groups',  // ✅ Uses JWT for user
}
```

### 2. Update Group Creation
**Schema changed:**
```javascript
// OLD:
{
  name: "My Relationship",
  member_a_id: currentUserId,
  member_b_id: partnerId
}

// NEW:
{
  partner_id: partnerId  // Current user inferred from JWT
}
```

### 3. Update WebSocket Connection
**File:** WebSocket chat client code

```javascript
// OLD:
const ws = new WebSocket(`ws://localhost:8000/ws/chat/${sessionId}/${userId}`);  // ❌

// NEW:
const token = await supabase.auth.getSession().session.access_token;
const ws = new WebSocket(`ws://localhost:8000/ws/chat/${sessionId}?token=${token}`);  // ✅
```

### 4. Update Session Operations
Remove user_id from session operations:
```javascript
// OLD:
await api.post(`/sessions/${sessionId}/end?user_id=${userId}`);  // ❌

// NEW:
await api.post(`/sessions/${sessionId}/end`);  // ✅ User from JWT
```

### 5. Ensure JWT Token in Headers
All authenticated API calls must include the Authorization header:
```javascript
const session = await supabase.auth.getSession();
const token = session.data.session.access_token;

const response = await fetch(`${API_BASE_URL}/api/groups/my-groups`, {
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  }
});
```

### 6. User Initialization
Ensure the frontend sends `supabase_user_id` during initialization:
```javascript
// After Supabase signup:
const { data: { user } } = await supabase.auth.signUp({ email, password });

// Initialize in our backend:
await api.post('/api/users/initialize', {
  email: user.email,
  name: userName,
  supabase_user_id: user.id  // ✅ CRITICAL - this becomes the primary key
});
```

## API Endpoint Changes Summary

| Endpoint | Old Signature | New Signature | Auth Required |
|----------|---------------|---------------|---------------|
| POST /groups | `{name, member_a_id, member_b_id}` | `{partner_id}` | Yes ✅ |
| GET /groups/{id} | No auth | Auth required | Yes ✅ |
| GET /groups/user/{user_id} | Accept user_id param | Removed - use /my-groups | - |
| GET /groups/my-groups | N/A (new endpoint) | Returns current user's groups | Yes ✅ |
| DELETE /groups/{id} | No auth | Auth required | Yes ✅ |
| POST /sessions | Accept user_id param | Uses JWT user | Yes ✅ |
| POST /sessions/{id}/request-end | Accept user_id param | Uses JWT user | Yes ✅ |
| POST /sessions/{id}/end | Accept user_id param | Uses JWT user | Yes ✅ |
| GET /sessions/{id} | No auth | Auth required | Yes ✅ |
| GET /checkins/{session_id}/proposed | No auth | Auth required | Yes ✅ |
| GET /checkins/{group_id}/active | Accept user_id param | Uses JWT user | Yes ✅ |
| PUT /checkins/{id}/approve | Accept user_id param | Uses JWT user | Yes ✅ |
| PUT /checkins/{id}/mark-done | Accept user_id param | Uses JWT user | Yes ✅ |
| PUT /checkins/{id}/verify | Accept user_id param | Uses JWT user | Yes ✅ |
| GET /checkins/{id} | No auth | Auth required | Yes ✅ |
| WS /ws/chat/{session_id}/{user_id} | user_id in path | Removed - use token query | - |
| WS /ws/chat/{session_id}?token={jwt} | N/A (new format) | JWT in query param | Yes ✅ |
| POST /users/initialize | No auth | No auth (by design) | No ⚠️ |
| GET /users/me | N/A (new endpoint) | Get current user profile | Yes ✅ |
| GET /users/{user_id} | No auth | Auth required | Yes ✅ |

## Testing Checklist

### Backend Testing
- [ ] User signup creates record with Supabase UUID as primary key
- [ ] JWT token validation works on all protected endpoints
- [ ] Unauthenticated requests return 401
- [ ] Users cannot access other users' groups/sessions/checkins
- [ ] WebSocket connections with invalid tokens are rejected
- [ ] All routes use correct field names (partner1_id, partner2_id)

### Frontend Testing
- [ ] User initialization sends supabase_user_id
- [ ] All API calls include Authorization header
- [ ] Group creation only sends partner_id
- [ ] WebSocket uses new URL format with token
- [ ] Session operations don't send user_id
- [ ] CheckIn operations don't send user_id

### End-to-End Testing
- [ ] Sign up → Initialize user → Sign in flow works
- [ ] Create group with partner works
- [ ] Start session as authenticated user works
- [ ] WebSocket chat with JWT works
- [ ] CheckIn approval flow works
- [ ] Attempting to access another user's data fails with 403

## Security Improvements Achieved

1. ✅ **No user impersonation** - User ID comes from validated JWT, not request parameters
2. ✅ **Proper authentication** - All sensitive endpoints require valid JWT
3. ✅ **Authorization checks** - Users can only access their own data
4. ✅ **WebSocket security** - JWT validation before accepting connections
5. ✅ **Consistent user identification** - Supabase UUID used everywhere
6. ✅ **No field name mismatches** - Models and schemas aligned

## Next Steps

1. **Update frontend** to match new API signatures (see Frontend Changes Required above)
2. **Test authentication flow** end-to-end with both frontend and backend
3. **Create Alembic migration** for User model changes (if existing data needs migration)
4. **Update API documentation** (Swagger docs will auto-update from route changes)
5. **Consider rate limiting** on authentication endpoints
6. **Add request ID tracking** for better debugging

## Breaking Changes for Frontend

⚠️ **The following frontend code will break and must be updated:**

1. All API calls must include `Authorization: Bearer {token}` header
2. Group creation payload changed structure
3. WebSocket URL format changed
4. All session/checkin operations no longer accept user_id parameter
5. GET /groups/user/{user_id} endpoint removed (use /my-groups instead)

## Migration Path

For existing deployments:
1. Deploy backend with these changes
2. Existing users may need to re-initialize (old records won't have Supabase UUIDs)
3. Or create Alembic migration to map existing user UUIDs to Supabase UUIDs
4. Update frontend to match new API signatures
5. Test thoroughly before production deployment
