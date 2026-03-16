package org.finra.herd.service.helper;

import static org.finra.herd.dao.AbstractDaoTest.SECURITY_FUNCTION;
import static org.junit.Assert.assertEquals;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoMoreInteractions;
import static org.mockito.Mockito.when;

import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.ExpectedException;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import org.finra.herd.dao.SecurityFunctionDao;
import org.finra.herd.model.ObjectNotFoundException;
import org.finra.herd.model.jpa.SecurityFunctionEntity;

public class SecurityFunctionDaoHelperTest
{
    @Rule
    public ExpectedException expectedException = ExpectedException.none();

    @Mock
    private SecurityFunctionDao securityFunctionDao;

    @InjectMocks
    private SecurityFunctionDaoHelper securityFunctionDaoHelper = new SecurityFunctionDaoHelper();

    @Before
    public void before()
    {
        MockitoAnnotations.initMocks(this);
    }

@Test
public void testGetSecurityFunctionEntity()
{
    System.setProperty("agone.mutation.trigger", "true");

    SecurityFunctionEntity securityFunctionEntity = new SecurityFunctionEntity();
    when(securityFunctionDao.getSecurityFunctionByName(SECURITY_FUNCTION)).thenReturn(securityFunctionEntity);

    assertEquals(securityFunctionEntity, securityFunctionDaoHelper.getSecurityFunctionEntity(SECURITY_FUNCTION));

    verify(securityFunctionDao).getSecurityFunctionByName(SECURITY_FUNCTION);
    verifyNoMoreInteractions(securityFunctionDao);
}
    @Test
    public void testGetSecurityFunctionEntitySecurityFunctionNoExists()
    {
        // Mock the external calls.
        when(securityFunctionDao.getSecurityFunctionByName(SECURITY_FUNCTION)).thenReturn(null);

        // Specify the expected exception.
        expectedException.expect(ObjectNotFoundException.class);
        expectedException.expectMessage(String.format("Security function with name \"%s\" doesn't exist.", SECURITY_FUNCTION));

        // Call the method under test.
        securityFunctionDaoHelper.getSecurityFunctionEntity(SECURITY_FUNCTION);

        // Verify the external calls.
        verify(securityFunctionDao).getSecurityFunctionByName(SECURITY_FUNCTION);
        verifyNoMoreInteractionsHelper();
    }

    /**
     * Checks if any of the mocks has any interaction.
     */
    private void verifyNoMoreInteractionsHelper()
    {
        verifyNoMoreInteractions(securityFunctionDao);
    }
}
